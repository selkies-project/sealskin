"""Dependencies that send a user's request to the node it concerns.

A frontend answers what it can itself and forwards the rest: a request about
a session goes to the node that runs it, one about a home directory to the
node that holds it, and a launch to the node placement picks. The forwarding
dependencies raise `cluster.Forwarded` with the other node's answer, which
the application turns into the response.

Requests another node forwarded here, and those of key-file clients, which
reach one node only, are always answered here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, Response

from . import audit, cluster, filesync, progress, quota, user_manager
from .fsutil import sanitize_for_filename
from .security import get_decrypted_request_body, verify_token
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

#: Least seconds between two syncs of a user's shared files for the file manager.
SHARED_SYNC_SECONDS = 15


def stays_here(user: dict[str, Any]) -> bool:
    """Whether a request is answered by this node whatever it concerns."""
    return bool(user.get("forwarded")) or user.get("via") == "key" or not cluster.is_clustered()


async def push_upload(node_id: str, username: str, upload_id: str) -> None:
    """Move the chunks of an upload this node received to the node that will use them.

    Raises:
        HTTPException: 503 when the node does not take them.
    """
    from .routers.uploads import upload_path

    directory = upload_path(username, upload_id)
    if not os.path.isdir(directory):
        return
    try:
        for name in sorted(os.listdir(directory)):
            with open(os.path.join(directory, name), "rb") as handle:
                answer = await cluster.call(
                    node_id,
                    "PUT",
                    f"/peer/upload/{username}/{upload_id}/{name}",
                    content=handle.read(),
                    timeout=300,
                )
            if answer.status_code >= 300:
                raise HTTPException(
                    status_code=503,
                    detail=f"The node the upload is for refused it: {answer.status_code} {answer.text[:200]}",
                )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"The node the upload is for did not take it: {exc}") from exc
    shutil.rmtree(directory, ignore_errors=True)


async def _send(request: Request, node_id: str, user: dict[str, Any], body: dict[str, Any] | None = None) -> None:
    """Forward a request, with the upload its body names, and raise the answer."""
    raw = None
    if body is None and request.method in ("POST", "PUT", "PATCH"):
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            body = None
    if isinstance(body, dict):
        if body.get("upload_id"):
            await push_upload(node_id, user["username"], str(body["upload_id"]))
        raw = json.dumps(body).encode()
    raise cluster.Forwarded(await cluster.forward(request, node_id, user, raw))


async def session_node(session_id: str, request: Request, user: dict[str, Any] = Depends(verify_token)) -> None:
    """Forward a request about a session this node does not run to the node that does."""
    if stays_here(user) or session_id in state.sessions:
        return
    node_id = await cluster.find_session(session_id)
    if node_id and node_id != cluster.NODE_ID:
        await _send(request, node_id, user)


def _home_owner(request: Request, user: dict[str, Any]) -> str:
    """Return the user a home directory route is about: the path's `username`, else the caller."""
    return request.path_params.get("username") or user["username"]


SHARED_FILES = "_sealskin_shared_files"
_shared_synced: dict[str, float] = {}


async def home_node(request: Request, user: dict[str, Any] = Depends(verify_token)) -> Any:
    """Forward a request about a home directory this node does not hold to the node that does.

    The directory is the route's `home_dir` or `home_name` path parameter.
    The user's shared files are on every node: a request about them is
    answered here, from a copy brought up to date at most every
    `SHARED_SYNC_SECONDS`, and what it changes is sent to the store after.
    """
    name = request.path_params.get("home_dir") or request.path_params.get("home_name")
    owner = _home_owner(request, user)
    if name == SHARED_FILES and not user.get("forwarded") and filesync.enabled():
        path = os.path.join(settings.storage_path, owner, SHARED_FILES)
        if time.monotonic() - _shared_synced.get(owner, 0.0) > SHARED_SYNC_SECONDS:
            _shared_synced[owner] = time.monotonic()
            await filesync.sync(owner, path)
        yield
        if request.method != "GET":
            _shared_synced.pop(owner, None)
            filesync.push_later(owner, path)
        return
    if not stays_here(user):
        node_id = cluster.node_of_home(owner, name) if name else None
        if node_id and node_id != cluster.NODE_ID:
            await _send(request, node_id, user)
    yield


async def body_home_node(
    request: Request,
    body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_token),
) -> None:
    """Forward a request whose body names a home directory (`home_name`) held by another node."""
    if stays_here(user):
        return
    name = body.get("home_name")
    node_id = cluster.node_of_home(user["username"], str(name)) if name else None
    if node_id and node_id != cluster.NODE_ID:
        await _send(request, node_id, user, body)


async def new_home_node(request: Request, user: dict[str, Any] = Depends(verify_token)) -> None:
    """Forward the creation of a home directory to the node placement picks for the user."""
    if stays_here(user):
        return
    owner = user_manager.get_user(_home_owner(request, user)) or user
    target = dict(owner, username=_home_owner(request, user), effective_settings=user["effective_settings"])
    node_id = cluster.choose_node(target)
    if node_id != cluster.NODE_ID:
        await _send(request, node_id, user)


def homes_of(username: str) -> list[str]:
    """Return a user's home directories on every node: those on this one and those the records place."""
    return sorted(set(user_manager.get_home_dirs(username)) | set(cluster.HOMES.get(username, {})))


def _launch_home(body: dict[str, Any], user: dict[str, Any]) -> str | None:
    """Return the persistent home directory a launch request mounts, if any."""
    effective = user.get("effective_settings") or {}
    home = body.get("home_name")
    if not effective.get("persistent_storage") or (home and str(home).lower() == "cleanroom"):
        return None
    app = state.installed_apps.get(str(body.get("application_id") or body.get("app_id") or ""))
    if app is not None and app.is_meta_app:
        return f"auto-{sanitize_for_filename(app.name)}"
    return str(home) if home else None


async def place_launch(
    request: Request,
    body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_token),
) -> Any:
    """Check a launch against the user's limits and forward it to the node it starts on.

    The launch is followed for `progress` when the request names it, and
    however it ends, here, in the handler, or on another node, is recorded.

    A forwarded launch asks the other node for any of its GPUs when the user
    picked one here, since devices are named per node. The request may name
    a `pool` or a `node` to start on.

    Raises:
        HTTPException: 403 past a limit, 503 when no node can take the session.
    """
    followed = progress.begin(body.get("launch_id"), user["username"])
    try:
        if not user.get("forwarded"):
            await _place(request, body, user)
        yield
    except cluster.Forwarded as answer:
        try:
            result = json.loads(answer.response.body or b"{}")
        except ValueError:
            result = {}
        if answer.response.status_code < 300:
            progress.finish(result, launch_id=followed)
        else:
            reason = str(result.get("detail") or f"The node answered {answer.response.status_code}.")
            progress.finish(error=reason, launch_id=followed)
        raise
    except HTTPException as exc:
        progress.finish(error=str(exc.detail), launch_id=followed)
        raise
    except Exception:
        progress.finish(error="An internal error occurred during application launch.", launch_id=followed)
        raise


async def follow_launch(
    body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_token),
) -> Any:
    """Follow a launch that always runs on this node for `progress`, and record how it ends."""
    followed = progress.begin(body.get("launch_id"), user["username"])
    try:
        yield
    except HTTPException as exc:
        progress.finish(error=str(exc.detail), launch_id=followed)
        raise
    except Exception:
        progress.finish(error="An internal error occurred during application launch.", launch_id=followed)
        raise


async def _place(request: Request, body: dict[str, Any], user: dict[str, Any]) -> None:
    """Check a launch against the user's limits and forward it when another node should run it."""
    if not cluster.is_approved():
        raise HTTPException(status_code=503, detail="This node is waiting for an administrator's approval.")
    await quota.check_launch(user)
    if user.get("via") == "key" or not cluster.is_clustered():
        if "runtime" not in cluster.roles():
            raise HTTPException(status_code=503, detail="This node runs no sessions.")
        return
    node_id = cluster.choose_node(
        user,
        wants_gpu=bool(body.get("selected_gpu")) and bool(user["effective_settings"].get("gpu")),
        home_name=_launch_home(body, user),
        pool=body.get("pool") or None,
        node=body.get("node") or None,
    )
    if node_id == cluster.NODE_ID:
        return
    sent = dict(body)
    if sent.get("selected_gpu"):
        sent["selected_gpu"] = "auto"
    progress.forwarded(node_id, cluster.NODES[node_id].get("name", node_id))
    await _send(request, node_id, user, sent)


async def storage_room(user: dict[str, Any] = Depends(verify_token)) -> None:
    """Refuse a request that adds to the storage of a user at their storage limit."""
    await quota.check_storage(user)


async def announce_changes(request: Request, user: dict[str, Any] = Depends(verify_token)) -> Any:
    """After an administrative request that may have changed the shared records, log it and tell the other nodes to look."""
    yield
    if request.method == "GET" or request.url.path.endswith(("/admin/data", "/admin/status")):
        return
    if not user.get("forwarded"):
        audit.record("admin", user["username"], method=request.method, path=request.url.path)
    if cluster.is_clustered():
        await cluster.announce()


async def gather(request: Request, user: dict[str, Any]) -> list[Any]:
    """Ask every other answering node the caller's request and return their JSON answers.

    A node that fails to answer is left out.
    """
    if stays_here(user):
        return []
    answers = []
    for node_id in cluster.members():
        if node_id == cluster.NODE_ID:
            continue
        try:
            answer = await cluster.forward(request, node_id, user, b"")
        except HTTPException:
            continue
        if answer.status_code == 200:
            try:
                answers.append(json.loads(answer.body))
            except ValueError:
                continue
    return answers


def forwarded_response(_request: Request, exc: cluster.Forwarded) -> Response:
    """Exception handler returning the answer a forwarding dependency carried."""
    return exc.response
