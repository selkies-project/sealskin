"""The cluster: this node's identity, the other nodes, and the calls between them.

Every node runs this same server. What a node does is its roles: a
`frontend` signs users in and proxies their sessions, a `runtime` runs
sessions. Nothing is a master; what the nodes share is the object store
(`store`), and what each knows of the others it asks them.

* **Identity.** A node is its server key: its id is a hash of the public key,
  and its record, `cluster/nodes/<id>.yml`, publishes that key, the address of
  its peer listener, and the certificate that listener serves. A node takes
  part once its record is approved; the first node of an empty store approves
  itself.
* **Peer calls.** A node calls another on its peer listener over TLS pinned to
  the certificates of the approved records, and signs each request with its
  server key (`sign_request`, `verify_request`). A frontend may make a call
  act as a user (`act`), which is how a user's request reaches the node that
  holds the session or home directory it is about (`forward`).
* **Live state.** Sessions stay with the node that runs them. Every node asks
  every other for its sessions and load each `peer_poll_seconds` and keeps the
  answers (`PEERS`), which placement, limits, and the dashboard read.
* **Joining.** A node with a bucket registers itself and waits for an
  administrator's approval. A node with a join code asks a node of the cluster
  (`join`), proves the code without sending it, and from then on keeps the
  shared records on the node that holds them (`store.PeerStore`).
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import ssl
import time
from typing import Any

import httpx
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import HTTPException, Request, Response

from . import audit, persistence, store, user_manager
from .settings import settings
from .state import state
from .version import __version__

logger = logging.getLogger(__name__)

#: Name every peer certificate is issued for, since nodes are dialed by address.
PEER_NAME = "sealskin-peer"
SCHEME = "SealSkin-Node"
TOKEN_SECONDS = 60
JOIN_CODE_SECONDS = 3600
#: Polls a node may miss before it counts as away.
MISSED_POLLS = 3
DEFAULT_POOL = "default"

NODE_ID = ""
#: Approved and pending node records keyed by node id.
NODES: dict[str, dict[str, Any]] = {}
#: Pool records keyed by name.
POOLS: dict[str, dict[str, Any]] = {}
#: The last answer of every other node: `{"seen": time, "missed": int, "status": dict}`.
PEERS: dict[str, dict[str, Any]] = {}
#: Home directory locations: user to home name to node id.
HOMES: dict[str, dict[str, str]] = {}
#: Called when the peer trust bundle changed and the proxy has to read it again.
reload_proxy: Any = None
#: Set to make the store watcher look at once.
store_wake = asyncio.Event()

_client: httpx.AsyncClient | None = None
_sync_client: httpx.Client | None = None
_trust_text = ""
_seen_tokens: dict[str, float] = {}


def _node_path(node_id: str) -> str:
    """Return the path of a node record."""
    return os.path.join(settings.cluster_path, "nodes", f"{node_id}.yml")


def _pool_path(name: str) -> str:
    """Return the path of a pool record."""
    return os.path.join(settings.cluster_path, "pools", f"{name}.yml")


def _state_file(name: str) -> str:
    """Return the path of one of this node's own state files."""
    return os.path.join(settings.node_state_path, name)


def roles() -> set[str]:
    """Return this node's roles."""
    return {role.strip().lower() for role in settings.node_roles.split(",") if role.strip()}


def node_name() -> str:
    """Return the name this node shows."""
    return settings.node_name or state.instance_name or os.uname()[1]


def peer_address() -> str:
    """Return the `host:port` other nodes reach this node's peer listener on."""
    if settings.node_address:
        return settings.node_address
    host, _port = user_manager.external_address()
    return f"{host}:{settings.peer_port}"


def public_url() -> str:
    """Return the URL browsers reach this node's web app on."""
    if settings.public_url:
        return settings.public_url.rstrip("/")
    host, port = user_manager.external_address()
    port = port or state.discovered_session_port or settings.session_port
    return f"https://{host}" if port == 443 else f"https://{host}:{port}"


# --- Identity ------------------------------------------------------------------


def _ensure_peer_certificate() -> str:
    """Return the PEM of this node's peer certificate, issuing key and certificate once."""
    cert_path = os.path.join(settings.node_state_path, "peer_cert.pem")
    key_path = os.path.join(settings.node_state_path, "peer_key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        with open(cert_path, encoding="utf-8") as handle:
            return handle.read()
    os.makedirs(settings.node_state_path, exist_ok=True, mode=0o700)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, PEER_NAME)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(PEER_NAME)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    with open(cert_path, "w", encoding="utf-8") as handle:
        handle.write(pem)
    return pem


def peer_certificate_paths() -> tuple[str, str, str]:
    """Return the paths of the peer certificate, its key, and the trust bundle Caddy reads."""
    base = settings.node_state_path
    return (
        os.path.join(base, "peer_cert.pem"),
        os.path.join(base, "peer_key.pem"),
        os.path.join(base, "peer_trust.pem"),
    )


def init() -> None:
    """Derive this node's id, issue its peer certificate, and resume a cluster it joined."""
    global NODE_ID
    der = state.server_private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    NODE_ID = hashlib.sha256(der).hexdigest()[:16]
    global _trust_text
    own = _ensure_peer_certificate()
    _cert, _key, trust_path = peer_certificate_paths()
    if os.path.exists(trust_path):
        # The proxy starts on the bundle of the last run, before the registry is read.
        with open(trust_path, encoding="utf-8") as handle:
            _trust_text = handle.read()
    if own.strip() not in _trust_text:
        _write_trust(own)
    joined = persistence.read_yaml(_state_file("join.yml"), {}) or {}
    if joined.get("store_node"):
        _use_store_node(joined["store_node"])


def self_record() -> dict[str, Any]:
    """Return what this node publishes about itself."""
    cert_path, _key, _trust = peer_certificate_paths()
    with open(cert_path, encoding="utf-8") as handle:
        certificate = handle.read()
    return {
        "id": NODE_ID,
        "name": node_name(),
        "public_key": state.server_public_key_pem,
        "peer_cert": certificate,
        "address": peer_address(),
        "public_url": public_url(),
        "session_domain": settings.session_domain,
        "roles": sorted(roles()),
        "version": __version__,
        "max_sessions": settings.node_max_sessions,
        "gpu_slots": settings.node_gpu_slots,
    }


def register_self() -> None:
    """Write this node's record, keeping what an administrator set on it.

    The first node of a store approves itself; any other waits for approval
    unless it joined with a code, which approved it already.
    """
    path = _node_path(NODE_ID)
    for _attempt in range(3):
        existing = persistence.read_yaml(path, None)
        record = dict(existing or {})
        record.update(self_record())
        if existing is None:
            others = [n for n in persistence.list_names(os.path.dirname(path)) if n != f"{NODE_ID}.yml"]
            record["approved"] = not others
            record["pool"] = settings.node_pool or DEFAULT_POOL
            record["joined_at"] = time.time()
        if record == existing:
            return
        try:
            persistence.write_yaml_sync(path, record)
            return
        except store.Conflict:
            continue
    logger.error("Could not write this node's record: another node keeps changing it.")


def is_approved(node_id: str | None = None) -> bool:
    """Whether a node, this one by default, is an approved member of the cluster."""
    return bool(NODES.get(node_id or NODE_ID, {}).get("approved"))


def is_clustered() -> bool:
    """Whether the cluster has an approved node besides this one."""
    return any(n.get("approved") for node_id, n in NODES.items() if node_id != NODE_ID)


# --- Registry ------------------------------------------------------------------


def _write_trust(text: str) -> bool:
    """Write the peer trust bundle when it changed and drop the clients built on the old one."""
    global _trust_text, _client, _sync_client
    if text == _trust_text:
        return False
    _trust_text = text
    _cert, _key, trust_path = peer_certificate_paths()
    os.makedirs(os.path.dirname(trust_path), exist_ok=True, mode=0o700)
    temp = trust_path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temp, trust_path)
    _client = None
    _sync_client = None
    return True


def load_registry() -> bool:
    """Read the node, pool, and home records and rebuild the peer trust bundle.

    Returns:
        `True` when the trust bundle changed, so the proxy has to reload it.
    """
    nodes: dict[str, dict[str, Any]] = {}
    directory = os.path.join(settings.cluster_path, "nodes")
    for name in persistence.list_names(directory):
        try:
            record = persistence.read_yaml(os.path.join(directory, name), None)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not read node record %s: %s", name, exc)
            continue
        if isinstance(record, dict) and record.get("id") and f"{record['id']}.yml" == name:
            nodes[record["id"]] = record
    pools: dict[str, dict[str, Any]] = {}
    directory = os.path.join(settings.cluster_path, "pools")
    for name in persistence.list_names(directory):
        try:
            record = persistence.read_yaml(os.path.join(directory, name), None)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not read pool record %s: %s", name, exc)
            continue
        if isinstance(record, dict):
            pools[name.removesuffix(".yml")] = record
    homes: dict[str, dict[str, str]] = {}
    directory = os.path.join(settings.cluster_path, "homes")
    for name in persistence.list_names(directory):
        try:
            record = persistence.read_yaml(os.path.join(directory, name), None)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(record, dict):
            homes[name.removesuffix(".yml")] = {str(k): str(v) for k, v in record.items()}
    NODES.clear()
    NODES.update(nodes)
    POOLS.clear()
    POOLS.update(pools)
    HOMES.clear()
    HOMES.update(homes)
    for node_id in list(PEERS):
        if node_id not in NODES:
            del PEERS[node_id]
    overrides = persistence.read_yaml(os.path.join(settings.cluster_path, "settings.yml"), {}) or {}
    settings.apply_overrides(overrides if isinstance(overrides, dict) else {})
    own = self_record()["peer_cert"]
    certificates = [own] + [
        n["peer_cert"] for node_id, n in sorted(NODES.items()) if node_id != NODE_ID and n.get("approved") and n.get("peer_cert")
    ]
    joined = persistence.read_yaml(_state_file("join.yml"), {}) or {}
    if joined.get("store_node", {}).get("peer_cert"):
        certificates.append(joined["store_node"]["peer_cert"])
    return _write_trust("\n".join(dict.fromkeys(c.strip() for c in certificates)) + "\n")


def apply_registry() -> None:
    """Read the cluster's records, put this node's own back if it is gone, and reload the proxy when the peer trust changed."""
    changed = load_registry()
    if NODE_ID not in NODES:
        register_self()
        changed = load_registry() or changed
    if changed and reload_proxy:
        reload_proxy()


def pool_of(node_id: str) -> str:
    """Return the pool a node is in."""
    return NODES.get(node_id, {}).get("pool") or DEFAULT_POOL


def pool_record(name: str) -> dict[str, Any]:
    """Return a pool's record, the defaults for a pool that has none."""
    return {"restricted": False, "cost": 1.0, "users": [], "groups": [], "domain": "", **POOLS.get(name, {})}


def may_use_pool(user: dict[str, Any], pool: str) -> bool:
    """Whether a user's sessions may run in `pool`.

    A pool a user's groups deny is closed to the user. A restricted pool is
    open only to administrators and to the users and groups it or the user's
    settings name.
    """
    effective = user.get("effective_settings") or {}
    if pool in (effective.get("pools_denied") or []):
        return False
    record = pool_record(pool)
    if not record.get("restricted") or user.get("is_admin"):
        return True
    return (
        pool in (effective.get("pools") or [])
        or user.get("username") in (record.get("users") or [])
        or bool(set(effective.get("groups") or []) & set(record.get("groups") or []))
    )


# --- Peer calls ----------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    """Return a context that trusts exactly the peer certificates of the approved nodes."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    context.load_verify_locations(cadata=_trust_text)
    return context


def sign_request(
    target: str, method: str, path: str, body: bytes | None, act: dict[str, Any] | None = None
) -> str:
    """Return the `Authorization` value of a peer request.

    Args:
        target: Id of the node called.
        method: HTTP method.
        path: Path and query as sent.
        body: Request body, or `None` for a streamed one, which is not bound.
        act: The user the call acts as: `username`, `provider_groups`,
            `is_admin`, and `via`.
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": NODE_ID,
        "aud": target,
        "iat": now,
        "exp": now + TOKEN_SECONDS,
        "jti": secrets.token_urlsafe(12),
        "m": method.upper(),
        "p": path,
        "b": hashlib.sha256(body).hexdigest() if body is not None else "-",
    }
    if act:
        claims["act"] = act
    return f"{SCHEME} {jwt.encode(claims, state.server_private_key, algorithm='RS256')}"


def _verify(token: str, public_key: str, method: str, path: str, body: bytes | None) -> dict[str, Any]:
    """Check a peer token against the request it came with and return its claims."""
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=NODE_ID,
            options={"require": ["exp", "iat", "iss", "aud", "jti"]},
            leeway=30,
        )
    except jwt.InvalidAudienceError as exc:
        raise HTTPException(
            status_code=421,
            detail=f"This request was signed for another node; this is node {NODE_ID} ('{node_name()}').",
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"The peer signature does not verify: {exc}") from exc
    if claims.get("m") != method.upper() or claims.get("p") != path:
        raise HTTPException(status_code=401, detail="The peer signature is for another request.")
    if claims.get("b") != "-" and (body is None or claims.get("b") != hashlib.sha256(body).hexdigest()):
        raise HTTPException(status_code=401, detail="The peer signature is for another body.")
    now = time.time()
    for jti in [j for j, until in _seen_tokens.items() if until < now]:
        del _seen_tokens[jti]
    if claims["jti"] in _seen_tokens:
        raise HTTPException(status_code=401, detail="The peer request was sent before.")
    _seen_tokens[claims["jti"]] = now + TOKEN_SECONDS + 60
    return claims


def on_peer_listener(request: Request) -> bool:
    """Whether a request arrived on the peer listener, as the proxy marks it."""
    return (
        request.headers.get("x-sealskin-listener") == "peer"
        and bool(state.proxy_secret)
        and hmac.compare_digest(request.headers.get("x-sealskin-secret", ""), state.proxy_secret)
    )


def via_proxy(request: Request) -> bool:
    """Whether a request came through this node's own proxy, so over TLS."""
    return bool(state.proxy_secret) and hmac.compare_digest(
        request.headers.get("x-sealskin-secret", ""), state.proxy_secret
    )


async def verify_request(request: Request, body: bytes | None = None, approved: bool = True) -> dict[str, Any]:
    """Authenticate a peer request and return `{"node": record, "claims": claims}`.

    Args:
        request: The request, which must have arrived on the peer listener.
        body: Its body when the caller read it already; read here otherwise,
            unless the signature leaves the body unbound.
        approved: Refuse a node whose record is not approved.

    Raises:
        HTTPException: 401 when the signature does not verify, 403 for a node
            that is unknown or not approved.
    """
    header = request.headers.get("authorization", "")
    if not on_peer_listener(request) or not header.startswith(SCHEME + " "):
        raise HTTPException(status_code=401, detail="Not a peer request.")
    token = header.split(" ", 1)[1]
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Malformed peer token.") from exc
    node = NODES.get(str(unverified.get("iss")))
    if not node or (approved and not node.get("approved")):
        raise HTTPException(status_code=403, detail="This node is not an approved member of the cluster.")
    if body is None and unverified.get("b") != "-":
        body = await request.body()
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return {"node": node, "claims": _verify(token, node["public_key"], request.method, path, body)}


def _address_of(node_id: str) -> str:
    """Return the peer address of a node, also of the store node before the registry is read."""
    node = NODES.get(node_id)
    if node:
        return node["address"]
    joined = persistence.read_yaml(_state_file("join.yml"), {}) or {}
    if joined.get("store_node", {}).get("id") == node_id:
        return joined["store_node"]["address"]
    raise HTTPException(status_code=502, detail=f"Node {node_id} is not known.")


def _prepare(
    node_id: str, method: str, path: str, body: bytes | None, act: dict[str, Any] | None, headers: dict[str, str] | None
) -> tuple[str, dict[str, str]]:
    """Return the URL and headers of a peer call."""
    sent = {k: v for k, v in (headers or {}).items() if v is not None}
    sent["authorization"] = sign_request(node_id, method, path, body, act)
    return f"https://{_address_of(node_id)}{path}", sent


async def call(
    node_id: str,
    method: str,
    path: str,
    *,
    json_body: Any = None,
    content: bytes | None = None,
    stream: Any = None,
    act: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """Call another node on its peer listener.

    Args:
        node_id: The node to call.
        method: HTTP method.
        path: Path and query.
        json_body: Body sent as JSON.
        content: Raw body.
        stream: Async byte iterator sent as the body, unbound by the signature.
        act: The user the call acts as (see `sign_request`).
        headers: Extra request headers.
        timeout: Seconds to wait for the answer.

    Raises:
        httpx.HTTPError: When the node does not answer.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(verify=_ssl_context())
    sent_headers = dict(headers or {})
    if json_body is not None:
        content = json.dumps(json_body).encode()
        sent_headers["content-type"] = "application/json"
    body = None if stream is not None else content or b""
    url, sent_headers = _prepare(node_id, method, path, body, act, sent_headers)
    return await _client.request(
        method,
        url,
        content=stream if stream is not None else body,
        headers=sent_headers,
        timeout=timeout,
        extensions={"sni_hostname": PEER_NAME},
    )


def call_sync(
    node_id: str, method: str, path: str, headers: dict[str, str] | None = None, content: bytes = b""
) -> httpx.Response:
    """Call another node from blocking code, as the peer store does."""
    global _sync_client
    if _sync_client is None:
        _sync_client = httpx.Client(verify=_ssl_context(), timeout=httpx.Timeout(15, connect=4))
    url, sent_headers = _prepare(node_id, method, path, content, None, headers)
    return _sync_client.request(
        method, url, content=content, headers=sent_headers, extensions={"sni_hostname": PEER_NAME}
    )


def acting(user: dict[str, Any]) -> dict[str, Any]:
    """Return the `act` claim for a user record `security.verify_token` returned."""
    return {
        "username": user["username"],
        "provider_groups": list(user.get("provider_groups") or []),
        "is_admin": bool(user.get("is_admin")),
        "via": user.get("via") or "",
    }


async def forward(request: Request, node_id: str, user: dict[str, Any], body: bytes | None = None) -> Response:
    """Send a user's request to the node it concerns and return that node's answer.

    Raises:
        HTTPException: 503 when the node does not answer.
    """
    if body is None:
        body = await request.body()
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    headers = {"content-type": request.headers.get("content-type")} if body else {}
    try:
        answer = await call(node_id, request.method, path, content=body, act=acting(user), headers=headers, timeout=900)
    except httpx.HTTPError as exc:
        name = NODES.get(node_id, {}).get("name", node_id)
        raise HTTPException(status_code=503, detail=f"Node '{name}' did not answer.") from exc
    kept = {k: v for k, v in answer.headers.items() if k.lower() in ("content-type", "content-disposition")}
    return Response(content=answer.content, status_code=answer.status_code, headers=kept)


class Forwarded(Exception):
    """Carries the answer of the node a request was forwarded to out of a dependency."""

    def __init__(self, response: Response) -> None:
        """Keep the answer."""
        super().__init__("forwarded")
        self.response = response


# --- Live state ----------------------------------------------------------------


def local_status() -> dict[str, Any]:
    """Return what this node tells the others about itself: its load and its sessions, without their secrets."""
    sessions = [
        {
            "session_id": session_id,
            "username": data.get("username"),
            "app_id": data.get("provider_app_id"),
            "app_name": data.get("app_name"),
            "created_at": data.get("created_at"),
            "gpu": bool(data.get("gpu_config")),
            "gpu_exclusive": bool(data.get("gpu_exclusive")),
            "is_collaboration": bool(data.get("is_collaboration")),
            "lab": bool(data.get("lab")),
        }
        for session_id, data in state.sessions.items()
    ]
    return {
        "id": NODE_ID,
        "name": node_name(),
        "version": __version__,
        "roles": sorted(roles()),
        "cpus": os.cpu_count() or 1,
        "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0,
        "gpus": [{"device": g.get("device"), "name": g.get("name") or g.get("model")} for g in state.available_gpus],
        "max_sessions": settings.node_max_sessions,
        "gpu_slots": settings.node_gpu_slots,
        "store_reachable": store.is_reachable(),
        "sessions": sessions,
        "time": time.time(),
    }


def status_of(node_id: str) -> dict[str, Any] | None:
    """Return the last known status of a node, this one's own being current."""
    if node_id == NODE_ID:
        return local_status()
    peer = PEERS.get(node_id)
    return peer["status"] if peer else None


def is_alive(node_id: str) -> bool:
    """Whether a node answered its recent polls."""
    if node_id == NODE_ID:
        return True
    peer = PEERS.get(node_id)
    return bool(peer and peer["missed"] < MISSED_POLLS)


def members(role: str | None = None, alive: bool = True) -> list[str]:
    """Return the ids of the approved nodes, optionally those with a role and those answering."""
    found = []
    for node_id, record in NODES.items():
        if not record.get("approved"):
            continue
        status = status_of(node_id)
        held = set(status["roles"]) if status else set(record.get("roles") or [])
        if role and role not in held:
            continue
        if alive and not is_alive(node_id):
            continue
        found.append(node_id)
    return found


async def _poll_one(node_id: str, timeout: float) -> None:
    """Ask one node for its status and note whether it answered."""
    entry = PEERS.setdefault(node_id, {"seen": 0.0, "missed": MISSED_POLLS, "status": None, "error": ""})
    name = NODES.get(node_id, {}).get("name", node_id)
    try:
        answer = await call(node_id, "GET", "/peer/status", timeout=timeout)
        if answer.status_code != 200:
            try:
                reason = str(answer.json().get("detail"))
            except ValueError:
                reason = answer.text[:200]
            raise ValueError(f"{NODES[node_id]['address']} answered {answer.status_code}: {reason}")
        if entry.get("error"):
            logger.info("Node '%s' is answering again.", name)
        entry.update(seen=time.time(), missed=0, status=answer.json(), error="")
    except (httpx.HTTPError, ValueError, HTTPException, KeyError) as exc:
        reason = str(exc) or type(exc).__name__
        # Said once per reason, so a node that is away does not fill the log.
        if reason != entry.get("error"):
            logger.warning("Node '%s' is not answering: %s", name, reason)
        entry["error"] = reason
        entry["missed"] = min(entry["missed"] + 1, MISSED_POLLS)


async def refresh_peers(timeout: float = 5.0) -> None:
    """Ask every other approved node for its status at once."""
    if not is_approved():
        return
    others = [n for n, record in NODES.items() if n != NODE_ID and record.get("approved")]
    if others:
        await asyncio.gather(*(_poll_one(node_id, timeout) for node_id in others))


async def announce(keys: list[str] | None = None) -> None:
    """Tell the other nodes the shared records changed, so they look now instead of at their next poll."""
    store_wake.set()

    async def tell(node_id: str) -> None:
        try:
            await call(node_id, "POST", "/peer/notify", json_body={"keys": keys or []}, timeout=5)
        except (httpx.HTTPError, HTTPException):
            pass

    others = [n for n in members() if n != NODE_ID]
    if others:
        await asyncio.gather(*(tell(node_id) for node_id in others))


def node_of_session(session_id: str) -> str | None:
    """Return the id of the node that runs a session, as last known."""
    if session_id in state.sessions:
        return NODE_ID
    for node_id, peer in PEERS.items():
        status = peer.get("status") or {}
        if any(s["session_id"] == session_id for s in status.get("sessions") or []):
            return node_id
    return None


async def find_session(session_id: str) -> str | None:
    """Return the node that runs a session, asking the others once when it is not known."""
    found = node_of_session(session_id)
    if found is None and is_clustered():
        await refresh_peers(timeout=3)
        found = node_of_session(session_id)
    return found


def sessions_of(username: str) -> list[dict[str, Any]]:
    """Return the sessions of a user across the cluster, each with its `node`; an App Laboratory session is not one."""
    found = []
    for node_id in members(alive=False):
        status = status_of(node_id) or {}
        found.extend(
            dict(s, node=node_id)
            for s in status.get("sessions") or []
            if s.get("username") == username and not s.get("lab")
        )
    return found


# --- Home directories ----------------------------------------------------------


def node_of_home(username: str, home_name: str) -> str | None:
    """Return the node that holds a user's home directory, when a record names one."""
    return HOMES.get(username, {}).get(home_name)


def record_home(username: str, home_name: str, node_id: str | None) -> None:
    """Record where a home directory is, or forget it with `node_id` `None`."""
    path = os.path.join(settings.cluster_path, "homes", f"{username}.yml")
    for _attempt in range(3):
        current = persistence.read_yaml(path, {}) or {}
        updated = {k: v for k, v in current.items() if k != home_name}
        if node_id:
            updated[home_name] = node_id
        if updated == current:
            break
        try:
            if updated:
                persistence.write_yaml_sync(path, updated)
            else:
                persistence.remove(path)
            break
        except store.Conflict:
            continue
    HOMES[username] = {str(k): str(v) for k, v in (persistence.read_yaml(path, {}) or {}).items()}


def adopt_local_homes() -> None:
    """Record the home directories on this node that no record places yet."""
    if "runtime" not in roles() or not is_approved():
        return
    try:
        users = sorted(os.listdir(settings.storage_path))
    except OSError:
        return
    for username in users:
        if not user_manager.get_user(username):
            continue
        for home in user_manager.get_home_dirs(username):
            if home.startswith("_") or node_of_home(username, home):
                continue
            record_home(username, home, NODE_ID)


# --- Placement -----------------------------------------------------------------


def _gpu_room(status: dict[str, Any], exclusive: bool) -> bool:
    """Whether a node can take one more GPU session, alone on a GPU when `exclusive`."""
    gpus = status.get("gpus") or []
    if not gpus:
        return False
    running = [s for s in status.get("sessions") or [] if s.get("gpu")]
    slots = status.get("gpu_slots") or 0
    if slots and len(running) >= slots:
        return False
    held_alone = sum(1 for s in running if s.get("gpu_exclusive"))
    if exclusive:
        return len(running) < len(gpus)
    return held_alone < len(gpus)


def choose_node(
    user: dict[str, Any],
    wants_gpu: bool = False,
    home_name: str | None = None,
    pool: str | None = None,
    node: str | None = None,
) -> str:
    """Pick the node a new session of `user` starts on.

    A named home directory fixes the node that holds it. Otherwise the
    candidates are the answering runtime nodes in pools the user may use,
    with room for the session, and the one with the lowest load per CPU wins,
    this node on a tie.

    Args:
        user: The user record `security.verify_token` returned.
        wants_gpu: The session asks for a GPU.
        home_name: Home directory the session mounts, if a persistent one.
        pool: Only consider nodes of this pool.
        node: Only consider this node.

    Raises:
        HTTPException: 503 when the home's node is away or no node has room,
            403 when the pool is closed to the user.
    """
    held_by = node_of_home(user["username"], home_name) if home_name else None
    if held_by and held_by in NODES:
        if not is_alive(held_by):
            name = NODES[held_by].get("name", held_by)
            raise HTTPException(status_code=503, detail=f"Home directory '{home_name}' is on node '{name}', which is not answering.")
        return held_by
    if pool and not may_use_pool(user, pool):
        raise HTTPException(status_code=403, detail=f"Pool '{pool}' is not open to this account.")
    exclusive = not (user.get("effective_settings") or {}).get("gpu_share", True)
    candidates: list[tuple[float, int, str]] = []
    for node_id in members(role="runtime"):
        if (node and node != node_id) or (pool and pool_of(node_id) != pool):
            continue
        if not may_use_pool(user, pool_of(node_id)):
            continue
        status = status_of(node_id) or {}
        running = status.get("sessions") or []
        if status.get("max_sessions") and len(running) >= status["max_sessions"]:
            continue
        if wants_gpu and not _gpu_room(status, exclusive):
            continue
        candidates.append((len(running) / max(status.get("cpus") or 1, 1), 0 if node_id == NODE_ID else 1, node_id))
    if not candidates:
        raise HTTPException(status_code=503, detail="No node open to this account has room for the session.")
    return min(candidates)[2]


# --- Joining -------------------------------------------------------------------


def _join_key(secret: str) -> bytes:
    """Return the key both sides of a join derive from the secret half of a join code."""
    return hashlib.sha256(f"sealskin-join {secret}".encode()).digest()


def _proof(key: bytes, record: dict[str, Any]) -> str:
    """Return the proof that the holder of `key` vouches for `record`."""
    return hmac.new(key, json.dumps(record, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def create_join_code(pool: str = DEFAULT_POOL) -> dict[str, Any]:
    """Issue a join code for one node, good for `JOIN_CODE_SECONDS`.

    Returns:
        `{"code": str, "expires": float, "join_url": str}`.
    """
    code_id, secret = secrets.token_hex(6), secrets.token_urlsafe(24)
    expires = time.time() + JOIN_CODE_SECONDS
    persistence.write_yaml_sync(
        os.path.join(settings.cluster_path, "join", f"{code_id}.yml"),
        {"key": _join_key(secret).hex(), "expires": expires, "pool": pool or DEFAULT_POOL},
    )
    return {"code": f"{code_id}.{secret}", "expires": expires, "join_url": peer_address()}


def accept_join(code_id: str, record: dict[str, Any], proof: str) -> dict[str, Any]:
    """Admit the node a join request describes, once per code.

    Returns:
        The record of the node that keeps the shared store and the proof the
        joining node checks it with.

    Raises:
        HTTPException: 403 for an unknown, spent, or expired code or a proof
            that does not verify.
    """
    refused = HTTPException(status_code=403, detail="The join code is not valid.")
    if not code_id.isalnum():
        raise refused
    path = os.path.join(settings.cluster_path, "join", f"{code_id}.yml")
    entry = persistence.read_yaml(path, None)
    if not isinstance(entry, dict):
        raise refused
    key = bytes.fromhex(entry["key"])
    der = serialization.load_pem_public_key(str(record.get("public_key", "")).encode()).public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if (
        entry.get("expires", 0) < time.time()
        or not hmac.compare_digest(proof, _proof(key, record))
        or hashlib.sha256(der).hexdigest()[:16] != record.get("id")
    ):
        raise refused
    persistence.remove(path)
    kept = {k: record.get(k) for k in self_record()}
    kept.update(approved=True, pool=entry.get("pool") or DEFAULT_POOL, joined_at=time.time())
    persistence.write_yaml_sync(_node_path(kept["id"]), kept)
    logger.info("Node '%s' (%s) joined with a join code.", kept.get("name"), kept["id"])
    audit.record("node_joined", node=kept["id"], name=kept.get("name"), pool=kept["pool"])
    joined = persistence.read_yaml(_state_file("join.yml"), {}) or {}
    keeper = joined.get("store_node") or self_record()
    return {"store_node": keeper, "proof": _proof(key, keeper)}


def _use_store_node(keeper: dict[str, Any]) -> None:
    """Keep the shared records on the node `keeper` describes."""
    _write_trust((_trust_text.strip() + "\n" + keeper["peer_cert"].strip() + "\n").lstrip())
    store.use_peer(lambda method, path, headers=None, content=b"": call_sync(keeper["id"], method, path, headers, content))


def join() -> None:
    """Ask the cluster `join_url` names to admit this node with `join_code`.

    The code never crosses the wire: each side proves it holds it over the
    record it sends, so the first contact needs no trusted certificate.

    Raises:
        RuntimeError: When the cluster refuses or its proof does not verify.
    """
    if persistence.read_yaml(_state_file("join.yml"), None) or not (settings.join_url and settings.join_code):
        return
    if "." not in settings.join_code:
        raise RuntimeError("SEALSKIN_JOIN_CODE is not a join code.")
    code_id, secret = settings.join_code.split(".", 1)
    key = _join_key(secret)
    record = self_record()
    try:
        answer = httpx.post(
            f"https://{settings.join_url}/peer/join",
            json={"code_id": code_id, "record": record, "proof": _proof(key, record)},
            verify=False,  # noqa: S501 - the proof below authenticates the answer
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach {settings.join_url} to join: {exc}") from exc
    if answer.status_code != 200:
        raise RuntimeError(f"{settings.join_url} refused the join: {answer.status_code} {answer.text[:200]}")
    body = answer.json()
    keeper = body.get("store_node") or {}
    if not hmac.compare_digest(str(body.get("proof", "")), _proof(key, keeper)):
        raise RuntimeError("The answer to the join does not prove the join code; refusing it.")
    os.makedirs(settings.node_state_path, exist_ok=True, mode=0o700)
    persistence.write_yaml_sync(_state_file("join.yml"), {"store_node": keeper, "joined_at": time.time()})
    _use_store_node(keeper)
    logger.info("Joined the cluster through %s; the shared records are kept on node '%s'.", settings.join_url, keeper.get("name"))


def may_write(node: dict[str, Any], key: str) -> bool:
    """Whether a node may write a shared object on the node that keeps the store.

    A frontend writes anything. A node that only runs sessions writes its own
    record, its usage, home directory locations, and shared files.
    """
    if "frontend" in (node.get("roles") or []):
        return True
    node_id = node["id"]
    return (
        key == f"cluster/nodes/{node_id}.yml"
        or (key.startswith("cluster/usage/") and key.endswith(f"/{node_id}.yml"))
        or key.startswith(("cluster/homes/", "files/"))
    )


def trusted_proxy(address: str) -> bool:
    """Whether `address` is one of the reverse proxies `trusted_proxies` names."""
    try:
        candidate = ipaddress.ip_address(address.strip("[]"))
    except ValueError:
        return False
    for entry in settings.trusted_proxies.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            if candidate in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False
