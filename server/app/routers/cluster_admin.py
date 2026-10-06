"""Administration of the cluster: nodes, pools, join codes, shared settings, and home moves."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError

from .. import audit, cluster, homes, persistence, proxy_auth, quota, routing, sso, store
from ..security import EncryptedRoute, get_decrypted_request_body, verify_admin, verify_token
from ..settings import CLUSTER_SETTINGS, settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/cluster",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)
user_router = APIRouter(prefix="/api/cluster", route_class=EncryptedRoute)

_NAME = re.compile(r"[a-zA-Z0-9_-]+")
SECRET_SETTINGS = ("oidc_client_secret",)


class NodeUpdate(BaseModel):
    """What an administrator sets on a node."""

    approved: bool | None = None
    pool: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]+$")


class PoolRecord(BaseModel):
    """A pool of nodes.

    Attributes:
        description: Shown in the dashboard.
        domain: Domain the pool's nodes are named under.
        restricted: Open only to the users and groups named here or allowed by their settings.
        users: Users the pool is open to when restricted.
        groups: Groups the pool is open to when restricted.
        cost: Allowance an hour of session costs in this pool.
        gpu_cost: Allowance an hour of GPU session costs; `cost` when unset.
        stop_when_spent: End the sessions of a user whose allowance is spent.
    """

    description: str = ""
    domain: str = ""
    restricted: bool = False
    users: list[str] = []
    groups: list[str] = []
    cost: float = Field(default=1.0, ge=0)
    gpu_cost: float | None = Field(default=None, ge=0)
    stop_when_spent: bool = False


class JoinCodeRequest(BaseModel):
    """A request for a join code."""

    pool: str = Field(default=cluster.DEFAULT_POOL, pattern=r"^[a-zA-Z0-9_-]+$")


class MoveHomeRequest(BaseModel):
    """Where to move a home directory."""

    node: str


def _parse(model: type[BaseModel], body: dict[str, Any]) -> Any:
    """Validate a request body or answer 422."""
    try:
        return model(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc


def _write(path: str, record: dict[str, Any]) -> None:
    """Write a shared record as this node last read it, answering 409 when another node changed it."""
    try:
        persistence.read_bytes(path)
        persistence.write_yaml_sync(path, record)
    except store.Conflict as exc:
        raise HTTPException(status_code=409, detail="Another node changed this record; reload and try again.") from exc
    except store.StoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _changed() -> None:
    """Reload the registry here; the router's dependency tells the other nodes."""
    cluster.apply_registry()


def _node_view(node_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return what the dashboard shows of a node."""
    status = cluster.status_of(node_id) or {}
    peer = cluster.PEERS.get(node_id) or {}
    sessions = status.get("sessions") or []
    return {
        "id": node_id,
        "name": record.get("name"),
        "address": record.get("address"),
        "public_url": record.get("public_url"),
        "roles": status.get("roles") or record.get("roles") or [],
        "pool": record.get("pool") or cluster.DEFAULT_POOL,
        "approved": bool(record.get("approved")),
        "self": node_id == cluster.NODE_ID,
        "alive": cluster.is_alive(node_id),
        "error": peer.get("error") or "",
        "last_seen": None if node_id == cluster.NODE_ID else peer.get("seen") or None,
        "version": status.get("version") or record.get("version"),
        "cpus": status.get("cpus"),
        "load": status.get("load"),
        "gpus": status.get("gpus") or [],
        "sessions": len(sessions),
        "gpu_sessions": sum(1 for s in sessions if s.get("gpu")),
        "max_sessions": status.get("max_sessions", record.get("max_sessions", 0)),
        "gpu_slots": status.get("gpu_slots", record.get("gpu_slots", 0)),
        "store_reachable": status.get("store_reachable", True),
    }


def _arrival(request: Request) -> dict[str, Any]:
    """Say how a request reached this node, for an administrator setting up a reverse proxy."""
    remote = request.headers.get("x-sealskin-remote", "")
    return {
        "remote": remote,
        "trusted": cluster.trusted_proxy(remote),
        "client": cluster.client_address(request),
        "host": request.headers.get("host", ""),
    }


@router.get("")
async def overview(request: Request) -> dict[str, Any]:
    """Return the nodes, pools, store, and shared settings of the cluster."""
    await cluster.refresh_peers(timeout=3)
    written = persistence.read_yaml(os.path.join(settings.cluster_path, "settings.yml"), {}) or {}
    pools = {name: cluster.pool_record(name) for name in {cluster.DEFAULT_POOL, *cluster.POOLS}}
    base = cluster.public_url()
    return {
        "node_id": cluster.NODE_ID,
        "nodes": [_node_view(node_id, record) for node_id, record in sorted(cluster.NODES.items(), key=lambda i: str(i[1].get("name")))],
        "pools": pools,
        "store": store.describe(),
        "settings": {
            name: ("" if name in SECRET_SETTINGS else getattr(settings, name)) for name in CLUSTER_SETTINGS
        },
        "written_settings": sorted(written),
        "secret_set": {name: bool(getattr(settings, name)) for name in SECRET_SETTINGS},
        "public_url": cluster.public_url(),
        "session_domain": settings.session_domain,
        "signin": {
            "enabled": sso.enabled(),
            "trusted_proxies": settings.trusted_proxies,
            "http_port": settings.http_port if settings.trusted_proxies.strip() else 0,
            "arrival": _arrival(request),
            "proxy_check": await proxy_auth.check() | {"unchecked": bool(settings.proxy_auth_unchecked)},
            "urls": {
                "oidc_redirect": f"{base}/api/auth/oidc/callback",
                "oidc_backchannel_logout": f"{base}/api/auth/oidc/backchannel-logout",
                "oidc_frontchannel_logout": f"{base}/api/auth/oidc/frontchannel-logout",
                **{f"saml_{name}": url for name, url in sso.sp_urls(base).items()},
            },
        },
    }


@router.post("/join_codes")
async def issue_join_code(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Issue a code one node can join with in the next hour."""
    req = _parse(JoinCodeRequest, body)
    if not store.is_local():
        current = store.describe()
        if current["kind"] == "s3":
            raise HTTPException(
                status_code=400,
                detail="With a bucket, a node joins by starting with the same SEALSKIN_STORE_URL; approve it here.",
            )
        raise HTTPException(status_code=400, detail="Issue join codes on the node that keeps the shared records.")
    return cluster.create_join_code(req.pool)


@router.put("/nodes/{node_id}")
async def update_node(node_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Approve or suspend a node, or move it to another pool."""
    req = _parse(NodeUpdate, body)
    record = cluster.NODES.get(node_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such node.")
    updated = dict(record)
    if req.approved is not None:
        if node_id == cluster.NODE_ID and not req.approved:
            raise HTTPException(status_code=400, detail="Suspend this node from another node.")
        updated["approved"] = req.approved
    if req.pool:
        updated["pool"] = req.pool
    _write(os.path.join(settings.cluster_path, "nodes", f"{node_id}.yml"), updated)
    await _changed()
    return _node_view(node_id, cluster.NODES[node_id])


@router.delete("/nodes/{node_id}", status_code=204)
async def remove_node(node_id: str) -> Response:
    """Remove a node's record; a node still running registers again, unapproved."""
    if node_id == cluster.NODE_ID:
        raise HTTPException(status_code=400, detail="Remove this node from another node.")
    if node_id not in cluster.NODES:
        raise HTTPException(status_code=404, detail="No such node.")
    persistence.remove(os.path.join(settings.cluster_path, "nodes", f"{node_id}.yml"))
    await _changed()
    return Response(status_code=204)


@router.put("/pools/{name}")
async def write_pool(name: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Create or replace a pool."""
    if not _NAME.fullmatch(name):
        raise HTTPException(status_code=400, detail="Invalid pool name.")
    req = _parse(PoolRecord, body)
    _write(os.path.join(settings.cluster_path, "pools", f"{name}.yml"), req.model_dump(exclude_none=True))
    await _changed()
    return cluster.pool_record(name)


@router.delete("/pools/{name}", status_code=204)
async def delete_pool(name: str) -> Response:
    """Delete a pool no node is in."""
    if any(cluster.pool_of(node_id) == name for node_id in cluster.NODES):
        raise HTTPException(status_code=409, detail="Move the pool's nodes to another pool first.")
    persistence.remove(os.path.join(settings.cluster_path, "pools", f"{name}.yml"))
    await _changed()
    return Response(status_code=204)


@router.put("/settings")
async def write_settings(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Write the cluster's shared settings; an empty value hands a setting back to each node's environment."""
    path = os.path.join(settings.cluster_path, "settings.yml")
    current = persistence.read_yaml(path, {}) or {}
    for name, value in body.items():
        if name not in CLUSTER_SETTINGS:
            raise HTTPException(status_code=400, detail=f"'{name}' is not a cluster setting.")
        if value is None or value == "":
            current.pop(name, None)
        else:
            current[name] = value
    _write(path, current)
    await _changed()
    return {"written_settings": sorted(current)}


@router.get("/usage")
async def usage(period: str = "month") -> dict[str, Any]:
    """Return the weighted hours every user spent in the running day, week, or month."""
    if period not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="The period is day, week, or month.")
    users: set[str] = set()
    for day in quota.period_days(period):
        for by_user in quota.USAGE.get(day, {}).values():
            users.update(by_user)
    return {"period": period, "hours": {user: round(quota.used_hours(user, period), 2) for user in sorted(users)}}


@router.get("/audit")
async def audit_log(
    request: Request,
    day: str = "",
    since: str = "",
    q: str = "",
    offset: int = 0,
    limit: int = 50,
    user: dict[str, Any] = Depends(verify_admin),
) -> dict[str, Any]:
    """Return audit events from every answering node, newest first, a page at a time.

    Args:
        request: The request, passed on to the other nodes.
        day: One day, `YYYY-MM-DD` in UTC; today when neither this nor `since` is given.
        since: Every day from this one to today.
        q: Words every returned event contains.
        offset: Events to skip, from the newest.
        limit: Events to return, 5000 at most; an export asks for them all.
        user: The administrator asking.

    Returns:
        `total` matching events, the `events` of the page, and the `days` any node has a log for.
    """
    if user.get("forwarded"):
        events = [dict(event, node=cluster.node_name()) for event in audit.read(day, q, since)]
        return {"events": events, "days": audit.days()}
    events = [dict(event, node=cluster.node_name()) for event in audit.read(day, q, since)]
    known = set(audit.days())
    for answer in await routing.gather(request, user):
        events.extend(answer.get("events") or [])
        known.update(answer.get("days") or [])
    # Reversed first, so events of one instant stay newest first through the stable sort.
    events.reverse()
    events.sort(key=lambda e: e.get("time", ""), reverse=True)
    offset, limit = max(0, offset), max(1, min(limit, 5000))
    return {"total": len(events), "offset": offset, "events": events[offset : offset + limit], "days": sorted(known, reverse=True)}


@router.post("/signin/test")
async def test_sign_in(request: Request, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Ask the configured identity provider for its metadata and say what came back.

    Body `{"kind": "oidc" | "saml" | "proxy"}`. Answers `ok`, and either what
    the provider published or the `error` reaching or reading it gave. For
    `proxy` it runs the check of the reverse proxy again (`proxy_auth.check`)
    and answers with its `state` and `detail`.
    """
    kind = body.get("kind")
    try:
        if kind == "oidc":
            if not (settings.oidc_issuer and settings.oidc_client_id):
                return {"ok": False, "error": "Set the issuer and the client ID first."}
            doc = await sso._discovery(fresh=True)
            return {
                "ok": True,
                "issuer": doc.get("issuer"),
                "authorization_endpoint": doc.get("authorization_endpoint"),
                "backchannel_logout": bool(doc.get("backchannel_logout_supported")),
                "scopes": doc.get("scopes_supported") or [],
                "claims": doc.get("claims_supported") or [],
            }
        if kind == "saml":
            if not settings.saml_metadata_url:
                return {"ok": False, "error": "No metadata URL is set."}
            idp = await sso._idp(fresh=True)
            return {
                "ok": True,
                "entity_id": idp.get("entity_id"),
                "sso_url": idp.get("sso_url"),
                "single_logout": bool(idp.get("slo")),
                "certificates": len(idp.get("certs") or []),
            }
        if kind == "proxy":
            if not proxy_auth.enabled():
                return {"ok": False, "error": "No user header is set."}
            found = await proxy_auth.check(fresh=True, cookies=request.headers.get("cookie", ""))
            return found | {"ok": found["state"] == "guarded", "error": found["detail"]}
    except Exception as exc:  # noqa: BLE001 - whatever went wrong is the answer
        return {"ok": False, "error": str(exc) or type(exc).__name__}
    raise HTTPException(status_code=400, detail="The kind is oidc, saml, or proxy.")


@router.get("/users/{username}/homes")
async def user_homes(username: str) -> dict[str, Any]:
    """Return which node holds each of a user's home directories."""
    return {"homes": dict(cluster.HOMES.get(username, {}))}


@router.post("/users/{username}/homedirs/{home_name}/move", dependencies=[Depends(routing.home_node)])
async def move_user_home(
    username: str, home_name: str, body: dict[str, Any] = Depends(get_decrypted_request_body)
) -> dict[str, str]:
    """Move a user's home directory to another node."""
    return await homes.migrate(username, home_name, _parse(MoveHomeRequest, body).node)


@user_router.get("")
async def my_cluster(user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """Return what a user may know of the cluster: the pools and nodes open to them and their own limits."""
    nodes = []
    for node_id in cluster.members(role="runtime", alive=False):
        pool = cluster.pool_of(node_id)
        if not cluster.may_use_pool(user, pool):
            continue
        status = cluster.status_of(node_id) or {}
        nodes.append(
            {
                "id": node_id,
                "name": cluster.NODES[node_id].get("name"),
                "pool": pool,
                "alive": cluster.is_alive(node_id),
                "gpus": status.get("gpus") or [],
            }
        )
    effective = user["effective_settings"]
    return {
        "clustered": cluster.is_clustered(),
        "node_id": cluster.NODE_ID,
        "nodes": nodes,
        "pools": sorted({n["pool"] for n in nodes}),
        "homes": dict(cluster.HOMES.get(user["username"], {})),
        "can_move_homes": bool(user.get("is_admin") or effective.get("home_migration")),
        "allowance": quota.allowance(user),
        "session_limit": effective.get("session_limit", -1),
        "sessions": len(cluster.sessions_of(user["username"])),
    }


@user_router.post("/homedirs/{home_name}/move", dependencies=[Depends(routing.home_node)])
async def move_my_home(
    home_name: str,
    body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_token),
) -> dict[str, str]:
    """Move one of the caller's home directories to another node open to them."""
    if not (user.get("is_admin") or user["effective_settings"].get("home_migration")):
        raise HTTPException(status_code=403, detail="Moving home directories is not allowed for this account.")
    target = _parse(MoveHomeRequest, body).node
    if not cluster.may_use_pool(user, cluster.pool_of(target)):
        raise HTTPException(status_code=403, detail="That node is not open to this account.")
    await asyncio.sleep(0)
    return await homes.migrate(user["username"], home_name, target)
