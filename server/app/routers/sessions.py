"""Session management for users plus the session proxy entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from .. import cluster, config_store, routing
from ..fsutil import resolve_within, safe_join, unique_filename
from ..launch import ephemeral_base, stop_session
from ..models import ActiveSessionInfo, SendFileToSessionRequest
from ..security import (
    OWN_ORIGIN_NEEDED,
    EncryptedRoute,
    canonical_uuid,
    get_decrypted_request_body,
    on_session_origin,
    relative,
    token_matches,
    verify_token,
)
from ..settings import settings
from ..state import state
from .uploads import reassemble_file

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/sessions",
    dependencies=[Depends(verify_token)],
    route_class=EncryptedRoute,
)
proxy_router = APIRouter()


def _home_of(data: dict[str, Any]) -> str:
    """Return the name of the persistent home directory a session mounts, or nothing for a cleanroom."""
    path = data.get("host_mount_path") or ""
    return "" if not path or path.startswith(ephemeral_base()) else os.path.basename(path)


def session_info(session_id: str, data: dict[str, Any], for_owner: bool = True) -> ActiveSessionInfo:
    """Build the session summary returned to clients.

    Args:
        session_id: Session id.
        data: Session record.
        for_owner: Use the collaboration controller URL for room sessions.

    Returns:
        The `ActiveSessionInfo`.
    """
    if for_owner and data.get("is_collaboration"):
        url = f"/room/{session_id}?token={data['controller_token']}"
    else:
        url = f"/{session_id}/?access_token={data['access_token']}"
    return ActiveSessionInfo(
        session_id=session_id,
        app_id=data["provider_app_id"],
        app_name=data["app_name"],
        app_logo=data["app_logo"],
        created_at=data["created_at"],
        session_url=url,
        launch_context=data.get("launch_context"),
        is_collaboration=data.get("is_collaboration", False),
        own_origin=bool(data.get("own_origin")),
        node=cluster.node_name(),
        home=_home_of(data),
        gpu=bool(data.get("gpu_config")),
    )


@router.get("", response_model=list[ActiveSessionInfo])
async def get_my_sessions(
    request: Request, user: dict[str, Any] = Depends(verify_token)
) -> list[ActiveSessionInfo]:
    """List the calling user's sessions on every node, newest first."""
    sessions = [
        session_info(sid, data)
        for sid, data in state.sessions.items()
        if data.get("username") == user["username"] and not data.get("lab")
    ]
    for answer in await routing.gather(request, user):
        sessions.extend(ActiveSessionInfo(**item) for item in answer)
    return sorted(sessions, key=lambda s: s.created_at, reverse=True)


@router.delete("/{session_id}", status_code=204, dependencies=[Depends(routing.session_node)])
async def stop_my_session(session_id: str, user: dict[str, Any] = Depends(verify_token)) -> Response:
    """Stop one of the calling user's sessions."""
    data = state.sessions.get(session_id)
    if not data or data.get("username") != user["username"]:
        raise HTTPException(status_code=404, detail="Session not found or permission denied.")
    await stop_session(session_id)
    return Response(status_code=204)


@router.post("/{session_id}/send_file", dependencies=[Depends(routing.session_node)])
async def send_file_to_session(
    session_id: str,
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_token),
) -> dict[str, str]:
    """Place an uploaded file into a running session's files directory."""
    try:
        req = SendFileToSessionRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc

    data = state.sessions.get(session_id)
    if not data or data.get("username") != user["username"]:
        raise HTTPException(status_code=404, detail="Session not found or permission denied.")
    host_mount_path = data.get("host_mount_path")
    if not host_mount_path:
        raise HTTPException(
            status_code=400, detail="Cannot send files to this session as it has no mounted storage."
        )

    reassembled_path = await reassemble_file(user["username"], req.upload_id, req.total_chunks)
    safe_filename = os.path.basename(req.filename)
    is_persistent = not host_mount_path.startswith(ephemeral_base())
    try:
        if is_persistent:
            dest_dir = safe_join(settings.storage_path, user["username"], "_sealskin_shared_files")
        else:
            dest_dir = resolve_within(
                ephemeral_base(),
                data.get("shared_files_path") or os.path.join(host_mount_path, "Desktop", "files"),
            )
    except ValueError as exc:
        os.remove(reassembled_path)
        raise HTTPException(status_code=400, detail="Session storage path is invalid.") from exc
    os.makedirs(dest_dir, exist_ok=True, mode=0o755)

    actual_filename = await asyncio.to_thread(unique_filename, dest_dir, safe_filename)
    try:
        file_location = safe_join(dest_dir, actual_filename)
    except ValueError as exc:
        os.remove(reassembled_path)
        raise HTTPException(status_code=400, detail="Invalid file name.") from exc
    try:
        await asyncio.to_thread(shutil.move, reassembled_path, file_location)
        await asyncio.to_thread(os.chmod, file_location, 0o644)
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(reassembled_path):
            os.remove(reassembled_path)
        logger.error("[%s] Failed to move reassembled file to session storage: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Could not place file in session storage.") from exc

    logger.info(
        "[%s] User '%s' wrote file '%s' (as '%s') to session.",
        session_id,
        user["username"],
        actual_filename,
        safe_filename,
    )
    return {"status": "success", "message": f"File '{safe_filename}' sent to session."}


def _known_collab_token(data: dict[str, Any], token: str | None) -> str | None:
    """Return the stored copy of a collaboration token, or `None` if unknown.

    Args:
        data: Session record.
        token: Token supplied by the client.

    Returns:
        The controller or viewer token as held in the session record.
    """
    if not token:
        return None
    candidates = [data.get("controller_token")] + [
        viewer.get("token") for viewer in data.get("viewers", [])
    ]
    for candidate in candidates:
        if token_matches(token, candidate):
            return candidate
    return None


@proxy_router.get("/{session_id:uuid}/")
async def initial_session_auth(session_id: uuid.UUID, request: Request) -> Response:
    """Exchange the one-time access token for the session cookie and redirect.

    The first exchange settles the origin the session is served from (see
    `on_session_origin`); a collaboration session of a key-file client stays
    on the shared origin, where its room frames it. A session of a web
    sign-in, room or not, is served on its own origin alone.
    """
    session_id_str = canonical_uuid(session_id)
    token = request.query_params.get("access_token")
    data = state.sessions.get(session_id_str)
    if not data or not token_matches(token, data.get("access_token")):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid session or token.")
    token = data["access_token"]
    own_origin = on_session_origin(request, session_id_str)
    if data.get("native") and not own_origin:
        raise HTTPException(status_code=403, detail=OWN_ORIGIN_NEEDED)
    if "own_origin" not in data and not (own_origin and data.get("is_collaboration")):
        data["own_origin"] = own_origin
        await config_store.save_sessions()
    if bool(data.get("own_origin")) != own_origin:
        raise HTTPException(status_code=403, detail="Forbidden: the session is served from another origin.")

    response = RedirectResponse(url=relative(request.url.remove_query_params("access_token")), status_code=303)
    is_embedded = request.query_params.get("embedded") == "true"
    samesite_policy = "none" if is_embedded else "lax"
    logger.info(
        "[%s] Initial auth successful on the %s origin. Setting session cookie (SameSite=%s) and redirecting.",
        session_id_str,
        "session's own" if own_origin else "shared",
        samesite_policy,
    )
    response.set_cookie(
        key=f"{settings.session_cookie_name}_{session_id_str}",
        value=token,
        httponly=True,
        secure=True,
        samesite=samesite_policy,
        path=f"/{session_id_str}",
    )
    collab_token = _known_collab_token(data, request.query_params.get("token"))
    if collab_token and data.get("is_collaboration"):
        response.set_cookie(
            key=f"collab_token_{session_id_str}",
            value=collab_token,
            path=f"/{session_id_str}",
            httponly=True,
            secure=True,
            samesite="none",
        )
    return response
