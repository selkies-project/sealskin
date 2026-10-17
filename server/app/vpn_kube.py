"""VPN gateways on Kubernetes: a hub pod per profile and endpoint, and a spoke beside every session.

A Docker node puts its sessions on an internal network beside the gateway;
pods have no such network, and a session pod may land on any node. So on
Kubernetes the gateway is a **hub**: a pod per profile and endpoint, owned
by the server like a session pod, that holds the one tunnel to the provider
and a WireGuard interface of its own behind a ClusterIP Service. A session
pod gets a native sidecar from the same image (`spoke` mode) that connects
to the hub over the pod network with a key pair of its own, makes that the
pod's default route, keeps the cluster's networks (`vpn_local_cidrs`) on the
pod's own route, and lets nothing else out. The hub forwards the spokes into
the tunnel and answers their DNS. One hub carries every session of the
deployment that picked its endpoint, wherever the pods run, so the
provider's connection is spent once per endpoint in use.

The hub's rendered files are a Secret the pod mounts: the profile's files,
the hub's private key, and the spokes' peers, which the server also adds to
the running hub through `pods/exec` so a session does not wait on the
kubelet's Secret sync. A hub that restarts reads its peers from the file,
and a hub pod that failed or was evicted is created again over the same
Secret and Service, so the spokes of its sessions reconnect with the keys
they have. The hub's objects carry the server's instance in their names and
labels, so two servers in one namespace keep hubs of their own. Spoke
addresses are taken from `vpn_hub_subnet`, the hub at `.1`, each session's
from a hash of its id.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import logging
import re
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from fastapi import HTTPException

from . import vpn
from .models import VpnTunnel
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

HUB_PORT = 51820
COMPONENT = "vpn-gateway"
#: Seconds a hub pod gets to come up, with its image pull.
READY_TIMEOUT = 300
#: Seconds a deleted hub pod gets to go, before its name is used again.
GONE_TIMEOUT = 60
#: Letters of the instance name a hub's name carries; longer names are cut and hashed to fit a DNS label.
INSTANCE_CHARS = 24


class Foreign(Exception):
    """An object under a hub's name that this server does not own."""


def _kube() -> Any:
    from .providers.kubernetes_provider import kube

    return kube()


def _errors() -> tuple[type[Exception], ...]:
    from .providers.kubernetes_provider import KubeError

    return (KubeError, httpx.HTTPError)


def _instance_tag() -> str:
    """Return the server's instance name as part of a DNS label, shortened with a hash when it is long."""
    name = state.instance_name or ""
    tag = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    if len(tag) > INSTANCE_CHARS:
        tag = f"{tag[: INSTANCE_CHARS - 7].rstrip('-')}-{hashlib.sha256(name.encode()).hexdigest()[:6]}"
    return tag


def _name(key: str) -> str:
    """Return the name of a hub's pod, Secret, and Service: the server's instance and the key."""
    tag = _instance_tag()
    return f"sealskin-vpn-{tag}-{key}" if tag else f"sealskin-vpn-{key}"


def _labels(key: str, profile_id: str, endpoint_id: str) -> dict[str, str]:
    from .providers.base_provider import INSTANCE_LABEL

    return {
        "app.kubernetes.io/name": "sealskin",
        "app.kubernetes.io/component": COMPONENT,
        INSTANCE_LABEL: state.instance_name,
        vpn.MANAGED_LABEL: vpn.MANAGED_VALUE,
        vpn.KEY_LABEL: key,
        vpn.PROFILE_LABEL: profile_id,
        vpn.ENDPOINT_LABEL: endpoint_id,
    }


def _instance_label() -> str:
    from .providers.base_provider import INSTANCE_LABEL

    return INSTANCE_LABEL


def _selector() -> str:
    return f"{vpn.MANAGED_LABEL}={vpn.MANAGED_VALUE},{_instance_label()}={state.instance_name}"


def keypair() -> tuple[str, str]:
    """Return a new WireGuard key pair as `(private, public)`, base64 as `wg` writes them."""
    private = x25519.X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode(), base64.b64encode(public).decode()


def hub_subnet() -> ipaddress.IPv4Network:
    """Return the network the hub and its spokes take their addresses from."""
    return ipaddress.ip_network(settings.vpn_hub_subnet, strict=False)


def hub_address() -> str:
    """Return the hub's own address on that network."""
    return str(hub_subnet()[1])


def spoke_address(who: str, taken: set[str]) -> str:
    """Return the address a session's spoke takes: from a hash of `who`, past the hub's and any in `taken`."""
    network = hub_subnet()
    size = network.num_addresses - 3
    offset = int(hashlib.sha256(who.encode()).hexdigest(), 16) % size
    for step in range(size):
        candidate = str(network[2 + (offset + step) % size])
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"No spoke address is left in vpn_hub_subnet ({settings.vpn_hub_subnet}).")


def spokes_in_use(key: str) -> set[str]:
    """Return the spoke addresses the sessions of this node hold on a hub."""
    return {s["vpn"]["spoke_address"] for s in state.sessions.values() if (s.get("vpn") or {}).get("key") == key and s["vpn"].get("spoke_address")}


def _encode(files: dict[str, str]) -> dict[str, str]:
    return {name: base64.b64encode(content.encode("utf-8")).decode() for name, content in files.items()}


def _decode(data: dict[str, str], name: str) -> str:
    return base64.b64decode(data.get(name, "")).decode("utf-8")


def hub_manifests(
    key: str, record: dict[str, Any], endpoint: dict[str, Any], files: dict[str, str], template: dict[str, Any] | None = None
) -> tuple[dict, dict, dict]:
    """Return the Secret, Pod, and Service of a hub.

    Args:
        key: The hub's key.
        record: The profile.
        endpoint: Its endpoint.
        files: The rendered files the Secret carries.
        template: The session PodTemplate's spec, whose pull secrets, tolerations, and node
            selector the hub pod takes, as session and export pods do.
    """
    api = _kube()
    name = _name(key)
    labels = _labels(key, record["id"], endpoint["id"])
    owner = [api.owner] if api.owner else []
    placement = template or {}
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "labels": labels, "ownerReferences": owner},
        "data": _encode(files),
    }
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "labels": labels, "ownerReferences": owner},
        "spec": {
            "restartPolicy": "Always",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 5,
            "nodeSelector": placement.get("nodeSelector") or {"kubernetes.io/os": "linux"},
            "containers": [
                {
                    "name": "gateway",
                    "image": settings.vpn_image,
                    "args": ["hub"],
                    "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
                    "readinessProbe": {
                        "exec": {"command": ["/usr/local/bin/sealskin-vpn", "health"]},
                        "periodSeconds": 5,
                        "initialDelaySeconds": 5,
                        "failureThreshold": 2,
                    },
                    "volumeMounts": [
                        {"name": "profile", "mountPath": "/run/sealskin-vpn", "readOnly": True},
                        {"name": "tun", "mountPath": "/dev/net/tun"},
                    ],
                }
            ],
            "volumes": [
                {"name": "profile", "secret": {"secretName": name, "defaultMode": 0o400}},
                {"name": "tun", "hostPath": {"path": "/dev/net/tun", "type": "CharDevice"}},
            ],
        },
    }
    for field in ("imagePullSecrets", "tolerations"):
        if placement.get(field):
            pod["spec"][field] = placement[field]
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "labels": labels, "ownerReferences": owner},
        "spec": {
            "selector": {vpn.KEY_LABEL: key, _instance_label(): state.instance_name},
            "ports": [{"name": "wireguard", "protocol": "UDP", "port": HUB_PORT, "targetPort": HUB_PORT}],
        },
    }
    return secret, pod, service


def _ready(pod: dict[str, Any]) -> bool:
    conditions = (pod.get("status") or {}).get("conditions") or []
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


async def _get(resource: str, name: str) -> dict[str, Any] | None:
    api = _kube()
    try:
        return await api.request("GET", api.path(resource, name))
    except _errors()[0] as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise


async def _own(resource: str, key: str) -> dict[str, Any] | None:
    """Return a hub's object by name, or `None` when there is none.

    Raises:
        Foreign: When what is under the name is not this server's hub, which is left alone.
    """
    found = await _get(resource, _name(key))
    if found is None:
        return None
    labels = found["metadata"].get("labels") or {}
    if labels.get(_instance_label()) != state.instance_name or labels.get(vpn.KEY_LABEL) != key:
        raise Foreign(f"{resource}/{_name(key)} in the namespace is not this server's VPN hub.")
    return found


async def _delete(resource: str, name: str) -> None:
    api = _kube()
    try:
        await api.request("DELETE", api.path(resource, name), params={"gracePeriodSeconds": 5})
    except _errors()[0] as exc:
        if getattr(exc, "status", None) != 404:
            raise


async def _remove_pod(key: str) -> None:
    """Delete a hub's pod and wait until its name is free, keeping its Secret and Service."""
    await _delete("pods", _name(key))
    deadline = time.monotonic() + GONE_TIMEOUT
    while time.monotonic() < deadline and await _get("pods", _name(key)) is not None:
        await asyncio.sleep(1)


async def _session_template() -> dict[str, Any]:
    """Return the spec of the namespace's session PodTemplate, or `{}` without one."""
    api = _kube()
    try:
        found = await api.request("GET", api.path("podtemplates", f"{state.instance_name}-session"))
    except _errors()[0] as exc:
        if getattr(exc, "status", None) in (403, 404):
            return {}
        raise
    return ((found or {}).get("template") or {}).get("spec") or {}


async def _wait_ready(key: str, record: dict[str, Any], endpoint: dict[str, Any]) -> dict[str, Any]:
    """Block until the hub pod is ready, or report why it is not.

    Raises:
        HTTPException: 502 when the hub does not come up in `READY_TIMEOUT` seconds, with its last words.
    """
    api = _kube()
    name = _name(key)
    deadline = time.monotonic() + READY_TIMEOUT
    pod: dict[str, Any] = {}
    while time.monotonic() < deadline:
        pod = await _get("pods", name) or {}
        if _ready(pod):
            return pod
        phase = (pod.get("status") or {}).get("phase")
        if phase in ("Failed", "Succeeded"):
            break
        await asyncio.sleep(2)
    last = ""
    try:
        log = await api.text("GET", api.path("pods", name) + "/log", params={"tailLines": "3"})
        last = log.strip().splitlines()[-1] if log.strip() else ""
    except _errors():
        pass
    reason = last or (pod.get("status") or {}).get("reason") or "the hub pod did not become ready"
    logger.error("VPN hub %s for '%s' (%s) did not come up: %s", name, record["name"], endpoint["label"], reason)
    if vpn.in_use(key):
        # Sessions keep their keys for the hub; the next launch or reconcile pass brings the pod back.
        await _remove_pod(key)
    else:
        await teardown(key)
    raise HTTPException(status_code=502, detail=f"The VPN '{record['name']}' ({endpoint['label']}) did not connect: {reason}")


def _ended(pod: dict[str, Any] | None) -> bool:
    """Whether a hub pod is going or has ended, and is to be created again."""
    return bool(pod) and bool(pod["metadata"].get("deletionTimestamp") or (pod.get("status") or {}).get("phase") in ("Failed", "Succeeded"))


async def _ensure_hub(record: dict[str, Any], endpoint: dict[str, Any], key: str) -> tuple[dict[str, Any], str, str]:
    """Return the hub's Secret, its Service address, and its public key, creating what is missing of the hub.

    A pod that ended is created again over the Secret it had, so the spokes keep their keys.

    Raises:
        Foreign: When an object under the hub's name is another server's.
    """
    api = _kube()
    name = _name(key)
    secret = await _own("secrets", key)
    pod = await _own("pods", key)
    if _ended(pod):
        logger.info("The VPN hub %s is %s; starting it again with its keys.", name, (pod.get("status") or {}).get("phase") or "going")
        await _remove_pod(key)
        pod = None
    if secret is None:
        cap = int(record.get("max_tunnels") or 0)
        if cap:
            held = len(await tunnels(record["id"]))
            if held >= cap:
                raise HTTPException(
                    status_code=409,
                    detail=f"'{record['name']}' already has {held} of its {cap} connections up across the deployment; pick an endpoint in use, or try later.",
                )
        private, public = keypair()
        files = vpn.render(record, endpoint, hub_address())
        files["env"] += f"HUB_ADDRESS={hub_address()}/{hub_subnet().prefixlen}\nHUB_PORT={HUB_PORT}\nLOCAL_CIDRS={settings.vpn_local_cidrs}\n"
        files["hub.key"] = private + "\n"
        files["hub.pub"] = public + "\n"
        files["peers"] = ""
        secret_manifest, _, _ = hub_manifests(key, record, endpoint, files)
        secret = await api.request("POST", api.path("secrets"), json=secret_manifest)
    if pod is None:
        from . import progress

        progress.step("vpn", vpn=record["name"], endpoint=endpoint["label"])
        _, pod_manifest, service_manifest = hub_manifests(key, record, endpoint, {}, await _session_template())
        if await _own("services", key) is None:
            await api.request("POST", api.path("services"), json=service_manifest)
        await api.request("POST", api.path("pods"), json=pod_manifest)
        logger.info("Started the VPN hub %s for '%s' (%s).", name, record["name"], endpoint["label"])
    service = await _own("services", key)
    if not service:
        raise HTTPException(status_code=500, detail="The VPN hub has no Service.")
    return secret, service["spec"]["clusterIP"], _decode(secret.get("data") or {}, "hub.pub").strip()


async def _set_peers(key: str, secret: dict[str, Any], peers: dict[str, str]) -> None:
    """Keep the hub's peers in its Secret, for the next time it starts."""
    api = _kube()
    content = "".join(f"{public} {address}/32\n" for public, address in sorted(peers.items()))
    await api.request(
        "PATCH",
        api.path("secrets", _name(key)),
        json={"data": {"peers": base64.b64encode(content.encode()).decode()}},
        headers={"Content-Type": "application/merge-patch+json"},
    )


def _peers_of(secret: dict[str, Any]) -> dict[str, str]:
    peers: dict[str, str] = {}
    for line in _decode(secret.get("data") or {}, "peers").splitlines():
        public, _, address = line.partition(" ")
        if public and address:
            peers[public] = address.split("/")[0]
    return peers


async def acquire(record: dict[str, Any], endpoint: dict[str, Any], who: str) -> dict[str, Any]:
    """Return what a session pod's spoke needs, starting the hub when it is not running.

    Args:
        record: The profile.
        endpoint: Its endpoint.
        who: What the spoke is for, which gives it its address: the session id, or the session
            and the application for another container of a room.

    Returns:
        `key`, `spoke_public_key`, `spoke_address`, and `spoke`, the environment of the sidecar.

    Raises:
        HTTPException: 409 at the profile's cap or when another server's object holds the hub's
            name, 502 when the hub does not come up.
    """
    key = vpn.key_for(record["id"], endpoint["id"])
    try:
        secret, service_ip, hub_public = await _ensure_hub(record, endpoint, key)
        await _wait_ready(key, record, endpoint)
        private, public = keypair()
        peers = _peers_of(secret)
        address = spoke_address(who, spokes_in_use(key) | set(peers.values()))
        peers[public] = address
        await _set_peers(key, secret, peers)
        await _kube().exec(_name(key), "gateway", ["wg", "set", "hub0", "peer", public, "allowed-ips", f"{address}/32"])
    except vpn.Unusable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Foreign as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except _errors() as exc:
        raise HTTPException(status_code=500, detail=f"Kubernetes error at the VPN hub: {exc}") from exc
    return {
        "key": key,
        "spoke_public_key": public,
        "spoke_address": address,
        "spoke": {
            "SPOKE_PRIVATE_KEY": private,
            "SPOKE_ADDRESS": f"{address}/32",
            "HUB_PUBLIC_KEY": hub_public,
            "HUB_ENDPOINT": f"{service_ip}:{HUB_PORT}",
            "HUB_ADDRESS": hub_address(),
            "LOCAL_CIDRS": settings.vpn_local_cidrs,
        },
    }


async def release(info: dict[str, Any]) -> None:
    """Take a session's spoke off its hub, and the hub down once no session or launch of this node uses it."""
    key = info.get("key") or ""
    if not key:
        return
    public = info.get("spoke_public_key")
    if public:
        try:
            secret = await _own("secrets", key)
            if secret:
                peers = _peers_of(secret)
                peers.pop(public, None)
                await _set_peers(key, secret, peers)
            await _kube().exec(_name(key), "gateway", ["wg", "set", "hub0", "peer", public, "remove"])
        except (Foreign, *_errors()) as exc:
            logger.info("Could not take the spoke off the VPN hub %s: %s", _name(key), exc)
    if not vpn.in_use(key):
        await teardown(key)


async def teardown(key: str) -> None:
    """Delete a hub's pod, Service, and Secret; a part already gone is not an error, another server's is left alone."""
    for resource in ("pods", "services", "secrets"):
        try:
            if await _own(resource, key) is not None:
                await _delete(resource, _name(key))
        except Foreign as exc:
            logger.warning("Not deleting %s", exc)
        except _errors() as exc:
            logger.error("Could not delete the VPN hub's %s %s: %s", resource, _name(key), exc)


def _hubs_in_use() -> dict[str, tuple[str, str]]:
    """Return the profile and endpoint of every hub a session of this node leaves through, by key."""
    found: dict[str, tuple[str, str]] = {}
    for session in state.sessions.values():
        for info in [session.get("vpn"), *(e.get("vpn") for e in (session.get("container_registry") or {}).values())]:
            if info and info.get("key") and info.get("profile") and info.get("endpoint"):
                found[info["key"]] = (info["profile"], info["endpoint"])
    return found


async def reconcile() -> None:
    """Keep the hubs of this node in step with its sessions.

    A hub no session or launch uses is taken down once it has been there for `vpn.ORPHAN_GRACE`;
    one whose sessions still exist and whose pod failed or is gone gets its pod again, over the
    Secret and Service it has, without waiting for it.
    """
    api = _kube()
    try:
        pods = await api.request("GET", api.path("pods"), params={"labelSelector": _selector()})
    except _errors() as exc:
        logger.warning("Could not list the VPN hubs: %s", exc)
        return
    from .providers.base_provider import epoch

    cutoff = time.time() - vpn.ORPHAN_GRACE
    used = _hubs_in_use()
    seen: set[str] = set()
    for pod in pods.get("items", []):
        key = (pod["metadata"].get("labels") or {}).get(vpn.KEY_LABEL) or ""
        if not key:
            continue
        seen.add(key)
        if not vpn.in_use(key) and epoch(pod["metadata"]["creationTimestamp"]) < cutoff:
            logger.info("Taking down the VPN hub %s, which no session uses.", pod["metadata"]["name"])
            await teardown(key)
        elif key in used and _ended(pod):
            await _restore(key, *used[key])
    for key in used.keys() - seen:
        await _restore(key, *used[key])


async def _restore(key: str, profile_id: str, endpoint_id: str) -> None:
    """Create the pod of a hub again for the sessions that leave through it, when its profile is still there."""
    record = state.vpn_profiles.get(profile_id)
    endpoint = vpn.endpoint_of(record, endpoint_id) if record else None
    if not record or not endpoint:
        logger.warning("The VPN hub %s has sessions but its profile is gone; leaving it.", _name(key))
        return
    async with vpn._lock(key):
        try:
            pod = await _own("pods", key)
            if pod is not None and not _ended(pod):
                return
            logger.info("Starting the VPN hub %s again for the sessions that use it.", _name(key))
            await _ensure_hub(record, endpoint, key)
        except (Foreign, HTTPException, vpn.Unusable, *_errors()) as exc:
            logger.error("Could not start the VPN hub %s again: %s", _name(key), getattr(exc, "detail", exc))


async def tunnels(profile_id: str) -> list[VpnTunnel]:
    """Return the hubs of a profile, each one connection to the provider."""
    from . import cluster

    api = _kube()
    record = state.vpn_profiles.get(profile_id) or {}
    try:
        pods = await api.request("GET", api.path("pods"), params={"labelSelector": f"{_selector()},{vpn.PROFILE_LABEL}={profile_id}"})
    except _errors():
        return []
    found = []
    for pod in pods.get("items", []):
        labels = pod["metadata"].get("labels") or {}
        if pod["metadata"].get("deletionTimestamp"):
            continue
        endpoint = vpn.endpoint_of(record, labels.get(vpn.ENDPOINT_LABEL, "")) or {}
        found.append(
            VpnTunnel(
                endpoint=labels.get(vpn.ENDPOINT_LABEL, ""),
                label=endpoint.get("label", "?"),
                node=cluster.node_name(),
                sessions=vpn.sessions_using(labels.get(vpn.KEY_LABEL, "")),
                state="healthy" if _ready(pod) else ((pod.get("status") or {}).get("phase") or "").lower(),
            )
        )
    return found + vpn.remote_tunnels(profile_id)
