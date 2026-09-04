"""Endpoints used only by Caddy's `forward_auth` on the loopback interface.

Caddy refuses `/internal/*` from clients; these handlers are reached only
through the sub-request Caddy makes to `127.0.0.1`.
"""

from __future__ import annotations

import base64
import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from ..settings import settings
from ..state import state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", include_in_schema=False)


@router.get("/resolve_session/{session_id}")
async def resolve_session(session_id: str, request: Request) -> Response:
    """Authorise a proxied request and tell Caddy where to send it.

    Returns:
        An empty 200 response with `X-Upstream-Host` and `X-Upstream-Auth`.

    Raises:
        HTTPException: 404 for unknown sessions, 403 for bad tokens.
    """
    token = request.query_params.get("access_token") or request.cookies.get(
        f"{settings.session_cookie_name}_{session_id}"
    )
    collab_token = request.query_params.get("token") or request.cookies.get(
        f"collab_token_{session_id}"
    )
    session = state.sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    is_standard_auth = bool(token) and secrets.compare_digest(token, session.get("access_token", ""))
    is_collab_controller = False
    is_collab_viewer = False
    if session.get("is_collaboration"):
        is_collab_controller = bool(collab_token) and collab_token == session.get("controller_token")
        is_collab_viewer = bool(collab_token) and any(
            v["token"] == collab_token for v in session.get("viewers", [])
        )
    if not (is_standard_auth or is_collab_controller or is_collab_viewer):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid session or token.")

    headers = {"X-Upstream-Host": f"{session['ip']}:{session['port']}"}
    if "custom_user" in session and "password" in session:
        auth_b64 = base64.b64encode(f"{session['custom_user']}:{session['password']}".encode()).decode()
        headers["X-Upstream-Auth"] = f"Basic {auth_b64}"
    return Response(status_code=200, headers=headers)
