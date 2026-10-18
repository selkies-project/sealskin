"""VPN profiles, and the gateways that carry sessions' traffic through them.

A profile is what an administrator imported from a provider's config files:
a template (an OpenVPN client config without its `remote` lines, or a
WireGuard `[Interface]`), the credentials the template asks for, and an
endpoint list with one entry per server the files named. A bundle of
thousands of files from a provider is one template and a long endpoint
list, because the files differ in the server alone; files whose template
differs become profiles of their own. The record is shared
(`vpn_profiles.yml`): every node reads the name, the endpoints, which of
them are published to users, the connection cap, and who may use the
profile. The template, the OpenVPN username and password, and the WireGuard
preshared keys are the profile's secrets: with a store key they are sealed
under the cluster key (`filecrypt.wrap`) before the record leaves the node,
and without one they are kept as the users' files are.

A session that chose a profile and an endpoint starts on an internal Docker
network of its own, which has no route out, beside a gateway container
(`vpn_image`) that holds the one tunnel to the endpoint, forwards the
sessions' traffic into it and nowhere else, and answers their DNS through
it. The server's own container joins the network to reach the session, the
session's `/etc/resolv.conf` names the gateway, and a one-shot helper from
the same image, run in the session's network namespace, points its default
route at the gateway. The session's image needs nothing for this, and a
gateway that is down leaves its sessions with no path out. One gateway per
profile and endpoint on a node carries every session that picked them, so a
provider's device limit is spent once per endpoint in use, and
`max_tunnels` caps the gateways of a profile on a node. A gateway starts
with the first session that needs it and stops with the last; its rendered
files live under `node_state_path/vpn/<key>` while it runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from . import filecrypt, persistence
from .models import VpnChoice, VpnEndpoint, VpnEndpointChoice, VpnProfileView, VpnTunnel
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

KINDS = ("wireguard", "openvpn")
#: Labels of a gateway container and its network; `INSTANCE_LABEL` of the providers marks the node.
MANAGED_LABEL = "sealskin.managed-by"
MANAGED_VALUE = "sealskin-vpn"
KEY_LABEL = "sealskin.vpn"
PROFILE_LABEL = "sealskin.vpn.profile"
ENDPOINT_LABEL = "sealskin.vpn.endpoint"
#: The gateway's host number on its `/24`; the sessions take the rest.
GATEWAY_HOST = 2
#: Seconds a gateway gets to bring its tunnel up.
HEALTHY_TIMEOUT = 120
#: Seconds a gateway no session references is left alone, while a launch may still claim it.
ORPHAN_GRACE = 90
#: What another node's gateway reports as: its sessions are known, its health is not.
REMOTE_STATE = "running"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 20000
CONFIG_SUFFIXES = (".ovpn", ".conf")
#: OpenVPN options the template does without: the server picks the endpoint, and Windows-only ones.
_OVPN_DROPPED = ("remote", "proto", "port", "remote-random", "block-outside-dns", "windows-driver", "nobind")
#: OpenVPN options that run a program or open a control channel in the gateway, which runs as root; dropped at import.
_OVPN_SCRIPTS = (
    "up",
    "down",
    "up-restart",
    "down-pre",
    "route-up",
    "route-pre-down",
    "ipchange",
    "tls-verify",
    "tls-export-cert",
    "auth-user-pass-verify",
    "client-connect",
    "client-disconnect",
    "learn-address",
    "plugin",
    "script-security",
    "dns-updown",
    "management",
    "management-client",
)
#: OpenVPN options that name a file, and the inline block that carries it instead.
_OVPN_FILES = {"ca": "ca", "cert": "cert", "key": "key", "tls-auth": "tls-auth", "tls-crypt": "tls-crypt", "tls-crypt-v2": "tls-crypt-v2", "crl-verify": "crl-verify"}
_OVPN_BINARY = ("pkcs12",)
#: WireGuard interface keys the template keeps; the gateway sets the routes and the rest.
_WG_INTERFACE = ("PrivateKey", "Address", "DNS", "MTU")

_locks: dict[str, asyncio.Lock] = {}
#: Launches that acquired a gateway and have not registered their session yet, by key.
_in_flight: dict[str, int] = {}


class Unusable(Exception):
    """A profile whose secrets this node cannot read."""


# --- The shared record ---------------------------------------------------------


def load() -> None:
    """Read the shared record into `state.vpn_profiles`."""
    data = persistence.read_yaml(settings.vpn_profiles_path, {}) or {}
    profiles = data.get("profiles") if isinstance(data, dict) else None
    state.vpn_profiles = {
        str(key): dict(value) for key, value in (profiles or {}).items() if isinstance(value, dict) and value.get("kind") in KINDS
    }


async def save() -> None:
    """Write `state.vpn_profiles` to the shared record."""
    await persistence.write_yaml(settings.vpn_profiles_path, {"profiles": state.vpn_profiles})


def _store_secret(record: dict[str, Any], secret: dict[str, Any]) -> None:
    """Keep a profile's secrets in its record, sealed when the cluster has a store key."""
    record.pop("secret", None)
    record.pop("sealed", None)
    if filecrypt.enabled():
        record["sealed"] = filecrypt.wrap(json.dumps(secret).encode("utf-8"))
    else:
        record["secret"] = secret


def _secret(record: dict[str, Any]) -> dict[str, Any]:
    """Return a profile's secrets.

    Raises:
        Unusable: When they were sealed under another store key, or are missing.
    """
    if isinstance(record.get("sealed"), dict):
        try:
            return json.loads(filecrypt.unwrap(record["sealed"], "The VPN profile's secret").decode("utf-8"))
        except filecrypt.Unreadable as exc:
            raise Unusable(str(exc)) from exc
    secret = record.get("secret")
    if not isinstance(secret, dict) or not secret.get("template"):
        raise Unusable("The VPN profile has no template.")
    return secret


def readable(record: dict[str, Any]) -> bool:
    """Whether this node can read a profile's secrets."""
    try:
        _secret(record)
    except Unusable:
        return False
    return True


def endpoint_of(record: dict[str, Any], endpoint_id: str) -> dict[str, Any] | None:
    """Return an endpoint of a profile by id."""
    return next((e for e in record.get("endpoints") or [] if e.get("id") == endpoint_id), None)


def offered(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the endpoints a profile offers users, in order: the published ones, else the first."""
    endpoints = record.get("endpoints") or []
    by_id = {e["id"]: e for e in endpoints}
    chosen = [by_id[i] for i in record.get("published") or [] if i in by_id]
    return chosen or endpoints[:1]


# --- Who may use what ----------------------------------------------------------


def _can_access(record: dict[str, Any], username: str, groups: list[str]) -> bool:
    """Whether a user may pick a profile: an administrator's `users`/`groups` lists, `all` for everyone."""
    users = record.get("users")
    allowed_groups = record.get("groups")
    users = ["all"] if users is None else users
    allowed_groups = ["all"] if allowed_groups is None else allowed_groups
    if "all" in users or "all" in allowed_groups or username in users:
        return True
    return any(group in allowed_groups for group in groups)


def choices_for(user: dict[str, Any]) -> list[VpnChoice]:
    """Return the profiles the launcher offers a user, with the endpoints each publishes."""
    effective = user.get("effective_settings") or {}
    if not effective.get("vpn", False) or not supported():
        return []
    groups = list(user.get("groups") or effective.get("groups") or [])
    choices = []
    for record in state.vpn_profiles.values():
        if not _can_access(record, user.get("username", ""), groups):
            continue
        choices.append(
            VpnChoice(
                id=record["id"],
                name=record["name"],
                kind=record["kind"],
                endpoints=[VpnEndpointChoice(id=e["id"], label=e["label"]) for e in offered(record)],
            )
        )
    return sorted(choices, key=lambda c: c.name.lower())


def choose(profile_id: str | None, endpoint_id: str | None, user: dict[str, Any] | None, effective: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Resolve a launch's VPN choice to a profile and an endpoint.

    Args:
        profile_id: The profile the launch named, or `None` for no VPN.
        endpoint_id: The endpoint it named, or `None` for the first offered.
        user: The record of the user launching, when a user is.
        effective: The user's effective settings.

    Returns:
        `(profile, endpoint)`, or `None` for a launch without a VPN.

    Raises:
        HTTPException: 403 when VPNs or this profile are closed to the user, 404 for an unknown
            profile or endpoint, 503 when this node cannot read the profile.
    """
    if not profile_id:
        return None
    if not effective.get("vpn", False):
        raise HTTPException(status_code=403, detail="VPNs are not open to this account.")
    record = state.vpn_profiles.get(profile_id)
    if not record:
        raise HTTPException(status_code=404, detail="That VPN profile is gone.")
    if user and not user.get("is_admin"):
        groups = list(user.get("groups") or effective.get("groups") or [])
        if not _can_access(record, user.get("username", ""), groups):
            raise HTTPException(status_code=403, detail="That VPN profile is not open to this account.")
    endpoints = offered(record)
    endpoint = next((e for e in endpoints if e["id"] == endpoint_id), None) if endpoint_id else (endpoints[0] if endpoints else None)
    if not endpoint:
        raise HTTPException(status_code=404, detail="That VPN endpoint is not offered.")
    if not readable(record):
        raise HTTPException(status_code=503, detail="This node cannot read the VPN profile's secrets; its store key differs.")
    return record, endpoint


# --- Importing provider bundles ------------------------------------------------


@dataclass
class Parsed:
    """A template and its endpoints, as one or more config files describe them.

    Attributes:
        kind: `wireguard` or `openvpn`.
        template: The config without what names a server.
        endpoints: The servers, each with `host`, `port`, `proto`, `label`, and for
            WireGuard `public_key`, `preshared_key`, `allowed_ips`.
        needs_credentials: Whether the OpenVPN template asks for a username and password.
        files: Names of the files the group came from.
    """

    kind: str
    template: str
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    needs_credentials: bool = False
    files: list[str] = field(default_factory=list)


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _unquote(value: str) -> str:
    """Strip the quotes an OpenVPN option value may carry."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _find_file(files: dict[str, bytes], name: str, near: str) -> bytes | None:
    """Return a support file a config names, looked for beside the config, then anywhere in the bundle by name."""
    name = _unquote(name)
    folder = os.path.dirname(near)
    for candidate in (os.path.normpath(os.path.join(folder, name)), name, os.path.basename(name)):
        if candidate in files:
            return files[candidate]
    base = os.path.basename(name)
    matches = [content for path, content in files.items() if os.path.basename(path) == base]
    return matches[0] if len(matches) == 1 else None


def _parse_openvpn(path: str, text: str, files: dict[str, bytes]) -> Parsed:
    """Reduce one OpenVPN config to a template and its remotes.

    Raises:
        ValueError: For a config that names a file the bundle lacks, or one the gateway cannot use.
    """
    lines = text.splitlines()
    kept: list[str] = []
    remotes: list[tuple[str, int | None, str | None]] = []
    default_port: int | None = None
    default_proto: str | None = None
    needs_credentials = False
    inline: str | None = None
    dropped: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if inline:
            kept.append(line)
            if line.strip() == f"</{inline}>":
                inline = None
            continue
        stripped = line.strip()
        if not stripped or stripped[0] in "#;":
            continue
        opened = re.fullmatch(r"<([a-z0-9-]+)>", stripped)
        if opened:
            inline = opened.group(1)
            kept.append(stripped)
            continue
        parts = stripped.split()
        option, args = parts[0], parts[1:]
        if option == "remote":
            if not args:
                raise ValueError(f"'{path}' has a 'remote' line without a host.")
            port = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
            proto = _proto(args[2]) if len(args) > 2 else None
            remotes.append((_unquote(args[0]), port, proto))
            continue
        if option == "port" and args and args[0].isdigit():
            default_port = int(args[0])
            continue
        if option == "proto" and args:
            default_proto = _proto(args[0])
            continue
        if option in _OVPN_DROPPED:
            continue
        if option in _OVPN_SCRIPTS:
            dropped.append(option)
            continue
        if option in _OVPN_BINARY:
            raise ValueError(f"'{path}' uses '{option}', which the gateway does not support; export a config with inline certificates.")
        if option == "auth-user-pass":
            needs_credentials = True
            kept.append("auth-user-pass")
            continue
        if option in _OVPN_FILES and args:
            content = _find_file(files, args[0], path)
            if content is None:
                raise ValueError(f"'{path}' refers to '{_unquote(args[0])}', which is not in the upload.")
            tag = _OVPN_FILES[option]
            try:
                body = content.decode("utf-8").strip()
            except UnicodeDecodeError as exc:
                raise ValueError(f"'{_unquote(args[0])}' is not a text file.") from exc
            kept.append(f"<{tag}>")
            kept.extend(body.splitlines())
            kept.append(f"</{tag}>")
            if option == "tls-auth" and len(args) > 1 and args[1] in ("0", "1"):
                kept.append(f"key-direction {args[1]}")
            continue
        kept.append(" ".join(parts))
    if inline:
        raise ValueError(f"'{path}' has an unterminated <{inline}> block.")
    if dropped:
        logger.warning("'%s': dropped %s, which the gateway does not run.", path, ", ".join(dict.fromkeys(dropped)))
    if not remotes:
        raise ValueError(f"'{path}' names no server (no 'remote' line).")
    endpoints = []
    seen: set[tuple[str, int, str]] = set()
    for host, port, proto in remotes:
        entry = (host, port or default_port or 1194, proto or default_proto or "udp")
        if entry in seen:
            continue
        seen.add(entry)
        endpoints.append({"host": entry[0], "port": entry[1], "proto": entry[2], "public_key": "", "preshared_key": "", "allowed_ips": ""})
    return Parsed("openvpn", "\n".join(kept) + "\n", endpoints, needs_credentials, [path])


def _proto(value: str) -> str:
    """Normalize an OpenVPN protocol word to `udp` or `tcp`."""
    return "tcp" if _unquote(value).lower().startswith("tcp") else "udp"


def _parse_wireguard(path: str, text: str) -> Parsed:
    """Reduce one WireGuard config to its interface and its peers.

    Raises:
        ValueError: For a config without a private key, an address, or a peer with an endpoint.
    """
    sections: list[tuple[str, dict[str, str]]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            sections.append((line[1:-1].strip().lower(), {}))
            continue
        if not sections or "=" not in line:
            raise ValueError(f"'{path}' is not a WireGuard config.")
        key, _, value = line.partition("=")
        sections[-1][1][key.strip()] = value.strip()
    interfaces = [body for name, body in sections if name == "interface"]
    peers = [body for name, body in sections if name == "peer"]
    if len(interfaces) != 1:
        raise ValueError(f"'{path}' needs exactly one [Interface] section.")
    interface = {key: value for key, value in interfaces[0].items() if key in _WG_INTERFACE}
    if not interface.get("PrivateKey") or not interface.get("Address"):
        raise ValueError(f"'{path}' needs a PrivateKey and an Address in its [Interface].")
    template = "".join(f"{key} = {interface[key]}\n" for key in _WG_INTERFACE if interface.get(key))
    endpoints = []
    for peer in peers:
        if not peer.get("PublicKey") or not peer.get("Endpoint"):
            raise ValueError(f"'{path}' has a [Peer] without a PublicKey or an Endpoint.")
        host, _, port = peer["Endpoint"].rpartition(":")
        host = host.strip("[]")
        if not host or not port.isdigit():
            raise ValueError(f"'{path}' has an Endpoint that is not host:port.")
        endpoints.append(
            {
                "host": host,
                "port": int(port),
                "proto": "udp",
                "public_key": peer["PublicKey"],
                "preshared_key": peer.get("PresharedKey", ""),
                "allowed_ips": peer.get("AllowedIPs") or "0.0.0.0/0",
            }
        )
    if not endpoints:
        raise ValueError(f"'{path}' has no [Peer].")
    return Parsed("wireguard", template, endpoints, False, [path])


def _bundle_files(filename: str, data: bytes) -> dict[str, bytes]:
    """Return the files of an upload: an archive's members, or the one file."""
    if len(data) > MAX_BUNDLE_BYTES:
        raise ValueError("The upload is larger than 64 MiB.")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return {filename: data}
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_FILES:
            raise ValueError(f"The archive holds more than {MAX_FILES} files.")
        for member in members:
            name = member.filename.replace("\\", "/")
            if name.startswith("__MACOSX/") or os.path.basename(name).startswith("."):
                continue
            if member.file_size > MAX_FILE_BYTES:
                continue
            files[name] = archive.read(member)
    return files


def _endpoint_key(endpoint: dict[str, Any]) -> tuple:
    return (endpoint["host"], endpoint["port"], endpoint["proto"], endpoint.get("public_key", ""))


def parse_bundle(filename: str, data: bytes) -> list[Parsed]:
    """Reduce an upload to its templates, each with the endpoints of the files that share it.

    Args:
        filename: Name of the uploaded file; an archive is told by its content.
        data: The upload.

    Returns:
        One `Parsed` per template, in the order their first file came.

    Raises:
        ValueError: When nothing in the upload is a config, or a config cannot be used.
    """
    files = _bundle_files(filename, data)
    configs = sorted(name for name in files if name.lower().endswith(CONFIG_SUFFIXES))
    if not configs:
        raise ValueError("Nothing in the upload is an OpenVPN (.ovpn) or WireGuard (.conf) config.")
    groups: dict[tuple[str, str], Parsed] = {}
    for path in configs:
        try:
            text = files[path].decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"'{path}' is not a text file.") from exc
        parsed = _parse_wireguard(path, text) if re.search(r"^\s*\[Interface\]", text, re.M | re.I) else _parse_openvpn(path, text, files)
        group = groups.setdefault((parsed.kind, parsed.template), parsed)
        if group is parsed:
            continue
        group.files.append(path)
        known = {_endpoint_key(e) for e in group.endpoints}
        group.endpoints.extend(e for e in parsed.endpoints if _endpoint_key(e) not in known)
    for group in groups.values():
        _label(group)
    return list(groups.values())


def _label(group: Parsed) -> None:
    """Give the endpoints of a group their labels: the host, the file's name for an address, a port where hosts repeat."""
    stems = [os.path.splitext(os.path.basename(f))[0] for f in group.files]
    for index, endpoint in enumerate(group.endpoints):
        host = endpoint["host"]
        if _is_address(host) and len(group.endpoints) == len(stems):
            endpoint["label"] = stems[index]
        else:
            endpoint["label"] = host
    counts: dict[str, int] = {}
    for endpoint in group.endpoints:
        counts[endpoint["label"]] = counts.get(endpoint["label"], 0) + 1
    for endpoint in group.endpoints:
        if counts[endpoint["label"]] > 1:
            endpoint["label"] = f"{endpoint['label']}:{endpoint['port']}/{endpoint['proto']}"


def _tag(group: Parsed, groups: list[Parsed]) -> str:
    """Return what sets a group apart from the others of its upload, for its name."""
    if len(groups) == 1:
        return ""
    protos = {e["proto"] for e in group.endpoints}
    if len(protos) == 1 and any({e["proto"] for e in other.endpoints} != protos for other in groups if other is not group):
        return protos.pop().upper()
    folder = os.path.basename(os.path.dirname(group.files[0]))
    return folder or os.path.splitext(os.path.basename(group.files[0]))[0]


def import_profiles(
    name: str,
    filename: str,
    data: bytes,
    username: str = "",
    password: str = "",
    users: list[str] | None = None,
    groups: list[str] | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Make profiles of an upload and add them to `state.vpn_profiles`; the caller saves.

    Args:
        name: Name of the profile; several templates make `<name> <tag>` each.
        filename: Name of the uploaded file.
        data: The upload.
        username: OpenVPN username, when the template asks for one.
        password: OpenVPN password.
        users: Usernames the profiles are open to (`all` for everyone).
        groups: Groups the profiles are open to.
        kind: The type the administrator chose, which every config must be; `None` takes what they are.

    Returns:
        The new records.

    Raises:
        ValueError: When the upload cannot be imported, a config is not of `kind`, or a template
            needs credentials not given.
    """
    parsed = parse_bundle(filename, data)
    other = next((p for p in parsed if kind and p.kind != kind), None)
    if other:
        found = "a WireGuard config" if other.kind == "wireguard" else "an OpenVPN config"
        raise ValueError(f"'{other.files[0]}' is {found}; pick that type, or upload a {kind.replace('wireguard', 'WireGuard').replace('openvpn', 'OpenVPN')} config.")
    if any(p.needs_credentials for p in parsed) and not username:
        raise ValueError("The config asks for a username and password; give the provider's service credentials.")
    records = []
    now = time.time()
    for group in parsed:
        tag = _tag(group, parsed)
        endpoints = []
        preshared: dict[str, str] = {}
        for endpoint in group.endpoints:
            endpoint_id = uuid.uuid4().hex[:12]
            if endpoint.get("preshared_key"):
                preshared[endpoint_id] = endpoint["preshared_key"]
            endpoints.append(
                VpnEndpoint(
                    id=endpoint_id,
                    label=endpoint["label"][:120],
                    host=endpoint["host"],
                    port=endpoint["port"],
                    proto=endpoint["proto"],
                    public_key=endpoint.get("public_key", ""),
                    allowed_ips=endpoint.get("allowed_ips") or "0.0.0.0/0",
                ).model_dump()
            )
        record: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "name": f"{name} {tag}".strip()[:80],
            "kind": group.kind,
            "endpoints": endpoints,
            "published": [],
            "max_tunnels": 0,
            "users": ["all"] if users is None else list(users),
            "groups": ["all"] if groups is None else list(groups),
            "needs_credentials": group.needs_credentials,
            "created": now,
            "revision": 1,
        }
        secret = {"template": group.template, "preshared_keys": preshared}
        if group.kind == "openvpn" and username:
            secret.update(username=username, password=password)
        _store_secret(record, secret)
        state.vpn_profiles[record["id"]] = record
        records.append(record)
    return records


def update_profile(record: dict[str, Any], changes: dict[str, Any]) -> None:
    """Apply an administrator's changes to a record; the caller saves.

    Args:
        record: The profile.
        changes: Fields of `VpnProfileUpdate` that were given.

    Raises:
        ValueError: For a published endpoint the profile lacks.
    """
    renamed = _apply_endpoints(record, changes["endpoints"]) if changes.get("endpoints") is not None else {}
    ids = {e["id"] for e in record.get("endpoints") or []}
    if changes.get("published") is not None:
        wanted = [renamed.get(i, i) for i in changes["published"]]
        unknown = [i for i in wanted if i not in ids]
        if unknown:
            raise ValueError(f"Unknown endpoint: {unknown[0]}")
        record["published"] = list(dict.fromkeys(wanted))
    for key in ("name", "max_tunnels", "users", "groups"):
        if changes.get(key) is not None:
            record[key] = changes[key]
    for endpoint_id, label in (changes.get("labels") or {}).items():
        endpoint = endpoint_of(record, endpoint_id)
        if endpoint and label.strip():
            endpoint["label"] = label.strip()[:120]
    if changes.get("username") is not None:
        secret = _secret(record)
        secret["username"] = changes["username"]
        secret["password"] = changes.get("password") or ""
        _store_secret(record, secret)
    record["revision"] = int(record.get("revision") or 0) + 1


def _apply_endpoints(record: dict[str, Any], edits: list[dict[str, Any]]) -> dict[str, str]:
    """Replace a profile's endpoints with the list as edited, keeping ids, keys, and the publish order.

    Returns:
        The id the record gave each new endpoint, keyed by the id the editor used for it.

    Raises:
        ValueError: For an endpoint without what its kind needs.
    """
    secret = _secret(record)
    keys: dict[str, str] = dict(secret.get("preshared_keys") or {})
    known = {e["id"]: e for e in record.get("endpoints") or []}
    renamed: dict[str, str] = {}
    endpoints = []
    for edit in edits:
        endpoint_id = edit.get("id") if edit.get("id") in known else uuid.uuid4().hex[:12]
        if edit.get("id") and edit["id"] not in known:
            renamed[edit["id"]] = endpoint_id
        if record["kind"] == "wireguard" and not edit.get("public_key"):
            raise ValueError(f"The endpoint {edit['host']}:{edit['port']} needs the server's public key.")
        endpoints.append(
            VpnEndpoint(
                id=endpoint_id,
                label=(edit.get("label") or "").strip()[:120] or edit["host"],
                host=edit["host"].strip(),
                port=int(edit["port"]),
                proto="udp" if record["kind"] == "wireguard" else edit.get("proto") or "udp",
                public_key=(edit.get("public_key") or "").strip(),
                allowed_ips=(edit.get("allowed_ips") or "0.0.0.0/0").strip(),
            ).model_dump(exclude={"has_preshared_key"})
        )
        if edit.get("preshared_key") is not None:
            if edit["preshared_key"].strip():
                keys[endpoint_id] = edit["preshared_key"].strip()
            else:
                keys.pop(endpoint_id, None)
    kept = {e["id"] for e in endpoints}
    record["endpoints"] = endpoints
    record["published"] = [i for i in record.get("published") or [] if i in kept]
    secret["preshared_keys"] = {k: v for k, v in keys.items() if k in kept}
    _store_secret(record, secret)
    return renamed


def add_endpoints(record: dict[str, Any], filename: str, data: bytes) -> int:
    """Add the endpoints of an upload to a profile and return how many were new.

    Raises:
        ValueError: When the upload cannot be parsed, or holds a config of another kind.
    """
    parsed = parse_bundle(filename, data)
    other = next((p for p in parsed if p.kind != record["kind"]), None)
    if other:
        raise ValueError(f"'{other.files[0]}' is not {'a WireGuard' if record['kind'] == 'wireguard' else 'an OpenVPN'} config.")
    secret = _secret(record)
    keys: dict[str, str] = dict(secret.get("preshared_keys") or {})
    known = {_endpoint_key(e) for e in record.get("endpoints") or []}
    added = 0
    for group in parsed:
        for endpoint in group.endpoints:
            if _endpoint_key(endpoint) in known:
                continue
            known.add(_endpoint_key(endpoint))
            endpoint_id = uuid.uuid4().hex[:12]
            if endpoint.get("preshared_key"):
                keys[endpoint_id] = endpoint["preshared_key"]
            record.setdefault("endpoints", []).append(
                VpnEndpoint(
                    id=endpoint_id,
                    label=endpoint["label"][:120],
                    host=endpoint["host"],
                    port=endpoint["port"],
                    proto=endpoint["proto"],
                    public_key=endpoint.get("public_key", ""),
                    allowed_ips=endpoint.get("allowed_ips") or "0.0.0.0/0",
                ).model_dump(exclude={"has_preshared_key"})
            )
            added += 1
    secret["preshared_keys"] = keys
    _store_secret(record, secret)
    record["revision"] = int(record.get("revision") or 0) + 1
    return added


def view(record: dict[str, Any], tunnels: list[VpnTunnel] | None = None) -> VpnProfileView:
    """Return a profile as the dashboard shows it."""
    has_credentials = False
    keys: dict[str, str] = {}
    try:
        secret = _secret(record)
        has_credentials = bool(secret.get("username"))
        keys = secret.get("preshared_keys") or {}
    except Unusable:
        pass
    return VpnProfileView(
        id=record["id"],
        name=record["name"],
        kind=record["kind"],
        endpoints=[VpnEndpoint(**{**e, "has_preshared_key": e["id"] in keys}) for e in record.get("endpoints") or []],
        published=list(record.get("published") or []),
        max_tunnels=int(record.get("max_tunnels") or 0),
        users=list(record.get("users") or ["all"]),
        groups=list(record.get("groups") or ["all"]),
        has_credentials=has_credentials,
        needs_credentials=bool(record.get("needs_credentials")),
        tunnels=tunnels or [],
        created=float(record.get("created") or 0),
    )


# --- Rendering a gateway's files -----------------------------------------------


def render(record: dict[str, Any], endpoint: dict[str, Any], gateway_ip: str) -> dict[str, str]:
    """Return the files a gateway reads for a profile and an endpoint, keyed by name.

    Raises:
        Unusable: When the profile's secrets cannot be read.
    """
    secret = _secret(record)
    env = {
        "KIND": record["kind"],
        "ENDPOINT_HOST": endpoint["host"],
        "ENDPOINT_PORT": str(endpoint["port"]),
        "ENDPOINT_PROTO": endpoint["proto"],
        "DNS_UPSTREAM": "",
        "WG_ADDRESS": "",
        "WG_MTU": "",
    }
    files: dict[str, str] = {"resolv.conf": f"nameserver {gateway_ip}\n"}
    if record["kind"] == "wireguard":
        interface = dict(line.partition("=")[::2] for line in secret["template"].splitlines() if "=" in line)
        interface = {k.strip(): v.strip() for k, v in interface.items()}
        env["WG_ADDRESS"] = interface.get("Address", "")
        env["WG_MTU"] = interface.get("MTU", "")
        env["DNS_UPSTREAM"] = " ".join(s.strip() for s in interface.get("DNS", "").split(",") if s.strip() and _is_address(s.strip()) and ":" not in s)
        peer = [f"PublicKey = {endpoint['public_key']}"]
        preshared = (secret.get("preshared_keys") or {}).get(endpoint["id"])
        if preshared:
            peer.append(f"PresharedKey = {preshared}")
        peer.extend([f"Endpoint = {endpoint['host']}:{endpoint['port']}", f"AllowedIPs = {endpoint.get('allowed_ips') or '0.0.0.0/0'}", "PersistentKeepalive = 25"])
        files["wg0.conf"] = f"[Interface]\nPrivateKey = {interface['PrivateKey']}\n\n[Peer]\n" + "\n".join(peer) + "\n"
    else:
        files["client.ovpn"] = secret["template"]
        if secret.get("username"):
            files["auth.txt"] = f"{secret['username']}\n{secret.get('password', '')}\n"
        pushed = re.findall(r"^dhcp-option DNS (\S+)", secret["template"], re.M)
        env["DNS_UPSTREAM"] = " ".join(p for p in pushed if _is_address(p))
    files["env"] = "".join(f"{key}={shlex.quote(value)}\n" for key, value in env.items())
    return files


# --- Gateways on this node -----------------------------------------------------


def key_for(profile_id: str, endpoint_id: str) -> str:
    """Return the key of the gateway of a profile and an endpoint, which names its container and network."""
    return hashlib.sha256(f"{profile_id}/{endpoint_id}".encode()).hexdigest()[:12]


def _name(key: str) -> str:
    return f"sealskin-vpn-{key}"


def _files_dir(key: str) -> str:
    return os.path.join(settings.node_state_path, "vpn", key)


def _lock(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


def _client() -> Any:
    from .docker_utils import get_docker_client

    return get_docker_client()


def _on_docker() -> bool:
    """Whether sessions run on Docker here, where gateways are containers beside them."""
    from .providers import get_provider
    from .providers.docker_provider import DockerProvider

    try:
        return isinstance(get_provider(), DockerProvider)
    except Exception:  # noqa: BLE001
        return False


def _on_kubernetes() -> bool:
    """Whether sessions run on Kubernetes here, where gateways are hubs (`app.vpn_kube`)."""
    from .providers import get_provider
    from .providers.kubernetes_provider import KubernetesProvider

    try:
        return isinstance(get_provider(), KubernetesProvider)
    except Exception:  # noqa: BLE001
        return False


def supported() -> bool:
    """Whether this node can route sessions through a profile."""
    return _on_docker() or _on_kubernetes()


def _labels(key: str, profile_id: str, endpoint_id: str) -> dict[str, str]:
    from .providers.base_provider import INSTANCE_LABEL

    return {MANAGED_LABEL: MANAGED_VALUE, INSTANCE_LABEL: state.instance_name, KEY_LABEL: key, PROFILE_LABEL: profile_id, ENDPOINT_LABEL: endpoint_id}


def _own_filter() -> dict[str, list[str]]:
    from .providers.base_provider import INSTANCE_LABEL

    return {"label": [f"{MANAGED_LABEL}={MANAGED_VALUE}", f"{INSTANCE_LABEL}={state.instance_name}"]}


def sessions_using(key: str) -> int:
    """Return how many sessions of this node leave through a gateway."""
    return sum(1 for s in state.sessions.values() if (s.get("vpn") or {}).get("key") == key)


def in_flight(key: str) -> int:
    """Return how many launches of this node hold a gateway for a session not registered yet."""
    return _in_flight.get(key, 0)


def in_use(key: str) -> int:
    """Return how many sessions and launches in progress of this node a gateway carries."""
    return sessions_using(key) + in_flight(key)


def launched(key: str | None) -> None:
    """Let go of the hold `acquire` took for a launch: its session is registered, or it gave up."""
    if not key:
        return
    left = _in_flight.get(key, 0) - 1
    if left > 0:
        _in_flight[key] = left
    else:
        _in_flight.pop(key, None)


def _free_subnet(client: Any) -> ipaddress.IPv4Network:
    """Pick the first `/24` of `vpn_subnets` no Docker network of the host overlaps."""
    pool = ipaddress.ip_network(settings.vpn_subnets, strict=False)
    used: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for network in client.networks.list():
        for config in (network.attrs.get("IPAM") or {}).get("Config") or []:
            try:
                used.append(ipaddress.ip_network(config["Subnet"], strict=False))
            except (KeyError, ValueError):
                continue
    for candidate in pool.subnets(new_prefix=24):
        if not any(candidate.version == u.version and candidate.overlaps(u) for u in used):
            return candidate
    raise RuntimeError(f"No free /24 is left in vpn_subnets ({settings.vpn_subnets}).")


def _write_files(key: str, files: dict[str, str]) -> str:
    """Write a gateway's rendered files under the node's state and return the folder."""
    folder = _files_dir(key)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    for name, content in files.items():
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        # The gateway runs as root and the session's resolv.conf is read by its user.
        os.chmod(path, 0o644 if name == "resolv.conf" else 0o600)
    return folder


def _info(key: str, network_name: str, gateway_ip: str) -> dict[str, Any]:
    from .docker_utils import translate_path_to_host

    return {
        "key": key,
        "network": network_name,
        "gateway_ip": gateway_ip,
        "resolv_conf": translate_path_to_host(os.path.join(_files_dir(key), "resolv.conf")),
    }


def _gateway_ip(container: Any, network_name: str) -> str:
    return ((container.attrs.get("NetworkSettings") or {}).get("Networks") or {}).get(network_name, {}).get("IPAddress") or ""


def _connect_server(client: Any, network: Any) -> None:
    """Join the server's own container to a gateway's network, so it reaches the sessions there."""
    from docker.errors import APIError, NotFound

    if not state.instance_name:
        return
    try:
        network.connect(state.instance_name)
    except NotFound:
        logger.info("The server is not a container here; it reaches %s through the host.", network.name)
    except APIError as exc:
        if "already exists" not in str(exc).lower():
            raise


def _teardown_sync(key: str) -> None:
    """Stop and remove a gateway, its network, and its files; a part already gone is not an error."""
    from docker.errors import APIError, NotFound

    client = _client()
    try:
        container = client.containers.get(_name(key))
        container.remove(force=True)
    except NotFound:
        pass
    try:
        network = client.networks.get(_name(key))
        if state.instance_name:
            try:
                network.disconnect(state.instance_name, force=True)
            except (NotFound, APIError):
                pass
        for container in network.containers:
            try:
                network.disconnect(container, force=True)
            except (NotFound, APIError):
                pass
        network.remove()
    except NotFound:
        pass
    shutil.rmtree(_files_dir(key), ignore_errors=True)


def _acquire_sync(record: dict[str, Any], endpoint: dict[str, Any], key: str) -> dict[str, Any]:
    """Start the gateway of a profile and an endpoint, or return the running one, and wait for its tunnel."""
    import docker.types
    from docker.errors import NotFound

    from . import progress
    from .docker_utils import local_image, translate_path_to_host

    client = _client()
    network_name = _name(key)
    try:
        existing = client.containers.get(network_name)
    except NotFound:
        existing = None
    if existing is not None:
        existing.reload()
        health = ((existing.attrs.get("State") or {}).get("Health") or {}).get("Status")
        if existing.status == "running" and health in ("healthy", "starting"):
            if health == "starting":
                _wait_healthy(existing, record, endpoint, key)
            return _info(key, network_name, _gateway_ip(existing, network_name))
        if in_use(key):
            # Sessions sit on its network; rebuilding it would cut them off, so it is brought back in place.
            if existing.status == "running":
                logger.info("The VPN gateway %s is %s; restarting it for the sessions on it.", network_name, health or "unhealthy")
                existing.restart(timeout=5)
            else:
                logger.info("The VPN gateway %s is %s; starting it again for the sessions on it.", network_name, existing.status)
                existing.start()
            _wait_healthy(existing, record, endpoint, key)
            existing.reload()
            return _info(key, network_name, _gateway_ip(existing, network_name))
        logger.info("The VPN gateway %s is %s; starting it again.", network_name, existing.status)
        _teardown_sync(key)

    cap = int(record.get("max_tunnels") or 0)
    if cap:
        held = len(tunnels_sync(record["id"], client))
        if held >= cap:
            raise HTTPException(
                status_code=409,
                detail=f"'{record['name']}' already has {held} of its {cap} connections up across the deployment; pick an endpoint in use, or try later.",
            )
    image = local_image(client, settings.vpn_image)
    progress.step("vpn", vpn=record["name"], endpoint=endpoint["label"])

    subnet = _free_subnet(client)
    gateway_ip = str(subnet[GATEWAY_HOST])
    files = render(record, endpoint, gateway_ip)
    folder = _write_files(key, files)
    labels = _labels(key, record["id"], endpoint["id"])
    network = client.networks.create(
        network_name,
        driver="bridge",
        internal=True,
        labels=labels,
        ipam=docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=str(subnet))]),
    )
    try:
        container = client.containers.create(
            image,
            name=network_name,
            labels=labels,
            cap_add=["NET_ADMIN"],
            devices=["/dev/net/tun:/dev/net/tun"],
            sysctls={"net.ipv4.ip_forward": "1", "net.ipv6.conf.all.disable_ipv6": "1"},
            volumes={translate_path_to_host(folder): {"bind": "/run/sealskin-vpn", "mode": "ro"}},
            restart_policy={"Name": "on-failure", "MaximumRetryCount": 5},
            network=network_name,
            networking_config={network_name: client.api.create_endpoint_config(ipv4_address=gateway_ip)},
        )
        client.networks.get(state.discovered_network or "bridge").connect(container)
        container.start()
        _connect_server(client, network)
        _wait_healthy(container, record, endpoint, key)
    except Exception:
        _teardown_sync(key)
        raise
    logger.info("VPN gateway %s is up for '%s' (%s) at %s.", network_name, record["name"], endpoint["label"], gateway_ip)
    return _info(key, network_name, gateway_ip)


def _wait_healthy(container: Any, record: dict[str, Any], endpoint: dict[str, Any], key: str) -> None:
    """Block until a gateway's health check passes.

    Raises:
        HTTPException: 502 when the tunnel does not come up in `HEALTHY_TIMEOUT` seconds, with the gateway's last words.
    """
    deadline = time.monotonic() + HEALTHY_TIMEOUT
    while time.monotonic() < deadline:
        container.reload()
        health = ((container.attrs.get("State") or {}).get("Health") or {}).get("Status")
        if health == "healthy":
            return
        if container.status in ("exited", "dead"):
            break
        time.sleep(2)
    try:
        last = container.logs(tail=3).decode("utf-8", "replace").strip().splitlines()
    except Exception:  # noqa: BLE001
        last = []
    reason = last[-1] if last else "no answer from the gateway"
    logger.error("VPN gateway %s for '%s' (%s) did not come up: %s", _name(key), record["name"], endpoint["label"], " | ".join(last))
    raise HTTPException(status_code=502, detail=f"The VPN '{record['name']}' ({endpoint['label']}) did not connect: {reason}")


async def acquire(record: dict[str, Any], endpoint: dict[str, Any], who: str) -> dict[str, Any]:
    """Return the gateway a session needs, starting it when it is not running.

    Args:
        record: The profile.
        endpoint: Its endpoint.
        who: The session, or the session and application, the gateway is for; a Kubernetes spoke
            takes its address from it.

    Returns:
        `key` and what the provider wires: on Docker `network`, `gateway_ip`, and `resolv_conf`,
        the host path of the file the session mounts as its `/etc/resolv.conf`; on Kubernetes the
        spoke's keys and environment (see `vpn_kube.acquire`). The gateway counts as in use until
        the caller registers the session or calls `launched`.

    Raises:
        HTTPException: 409 at the profile's cap, 502 when the tunnel does not come up, 503 when the
            profile's secrets cannot be read.
    """
    key = key_for(record["id"], endpoint["id"])
    async with _lock(key):
        if _on_kubernetes():
            from . import vpn_kube

            info = await vpn_kube.acquire(record, endpoint, who)
        else:
            try:
                info = await asyncio.to_thread(_acquire_sync, record, endpoint, key)
            except Unusable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        # Held until the launch registers its session, so a release in between leaves the gateway up.
        _in_flight[key] = _in_flight.get(key, 0) + 1
        return info


async def release(info: dict[str, Any] | None) -> None:
    """Let go of what a session held: its spoke on Kubernetes, and the gateway once no session or launch of this node uses it.

    Args:
        info: The session's `vpn` record, with the gateway's `key`.
    """
    key = (info or {}).get("key") or ""
    if not key:
        return
    if _on_kubernetes():
        from . import vpn_kube

        async with _lock(key):
            await vpn_kube.release(info or {})
        return
    async with _lock(key):
        if in_use(key):
            return
        try:
            await asyncio.to_thread(_teardown_sync, key)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not stop the VPN gateway %s: %s", _name(key), exc)


def _running_gateways(client: Any) -> list[Any]:
    """Return this node's gateway containers."""
    return client.containers.list(all=True, filters=_own_filter())


async def reconcile() -> None:
    """Keep the gateways of this node in step with its sessions.

    A gateway no session or launch references is stopped once it has been there for `ORPHAN_GRACE`;
    one that exited while sessions still reference it is started again, with the files it was
    rendered from.
    """
    if _on_kubernetes():
        from . import vpn_kube

        await vpn_kube.reconcile()
        return
    if not _on_docker():
        return
    try:
        client = _client()
        gateways = await asyncio.to_thread(_running_gateways, client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list the VPN gateways: %s", exc)
        return
    from .providers.base_provider import epoch

    cutoff = time.time() - ORPHAN_GRACE
    for container in gateways:
        key = (container.labels or {}).get(KEY_LABEL) or ""
        if not key:
            continue
        if not in_use(key):
            if epoch(container.attrs["Created"]) < cutoff:
                logger.info("Stopping the VPN gateway %s, which no session uses.", container.name)
                await release({"key": key})
            continue
        if container.status in ("exited", "dead"):
            logger.info("Starting the VPN gateway %s again for the sessions that use it.", container.name)
            try:
                await asyncio.to_thread(container.start)
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not start the VPN gateway %s: %s", container.name, exc)


def remote_tunnels(profile_id: str) -> list[VpnTunnel]:
    """Return the gateways of a profile the other nodes report through their sessions.

    A gateway there that just lost its last session is not counted, and its
    health is not known.
    """
    from . import cluster

    record = state.vpn_profiles.get(profile_id) or {}
    found: list[VpnTunnel] = []
    for node_id in cluster.members():
        if node_id == cluster.NODE_ID:
            continue
        status = cluster.status_of(node_id) or {}
        counts: dict[str, int] = {}
        for session in status.get("sessions") or []:
            using = session.get("vpn") or {}
            if using.get("profile") == profile_id and using.get("endpoint"):
                counts[using["endpoint"]] = counts.get(using["endpoint"], 0) + 1
        for endpoint_id, sessions in sorted(counts.items()):
            endpoint = endpoint_of(record, endpoint_id) or {}
            found.append(VpnTunnel(endpoint=endpoint_id, label=endpoint.get("label", "?"), node=status.get("name") or node_id, sessions=sessions, state=REMOTE_STATE))
    return found


async def tunnels(profile_id: str) -> list[VpnTunnel]:
    """Return the gateways of a profile up across the deployment, each one connection to the provider."""
    if _on_kubernetes():
        from . import vpn_kube

        return await vpn_kube.tunnels(profile_id) + remote_tunnels(profile_id)
    return await asyncio.to_thread(tunnels_sync, profile_id)


def tunnels_sync(profile_id: str, client: Any | None = None) -> list[VpnTunnel]:
    """Return the gateways of a profile up across the deployment: this node's as Docker shows them, the others' as their sessions report them."""
    from . import cluster

    record = state.vpn_profiles.get(profile_id) or {}
    tunnels: list[VpnTunnel] = []
    if _on_docker():
        try:
            gateways = _running_gateways(client or _client())
        except Exception:  # noqa: BLE001
            gateways = []
        for container in gateways:
            labels = container.labels or {}
            if labels.get(PROFILE_LABEL) != profile_id or container.status != "running":
                continue
            endpoint = endpoint_of(record, labels.get(ENDPOINT_LABEL, "")) or {}
            health = ((container.attrs.get("State") or {}).get("Health") or {}).get("Status") or container.status
            tunnels.append(
                VpnTunnel(
                    endpoint=labels.get(ENDPOINT_LABEL, ""),
                    label=endpoint.get("label", "?"),
                    node=cluster.node_name(),
                    sessions=sessions_using(labels.get(KEY_LABEL, "")),
                    state=health,
                )
            )
    return tunnels + remote_tunnels(profile_id)
