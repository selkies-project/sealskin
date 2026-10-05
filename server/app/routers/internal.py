"""Endpoints used only by Caddy's `forward_auth` on the loopback interface.

Caddy refuses `/internal/*` from clients; these handlers are reached only
through the sub-request Caddy makes to `127.0.0.1`.
"""

from __future__ import annotations

import base64
import glob
import html
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from .. import cluster
from ..providers.base_provider import host_port
from ..security import on_session_origin, token_matches
from ..settings import settings
from ..state import state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", include_in_schema=False)

STOPPED_PAGE = '<!doctype html><meta charset="utf-8"><title>SealSkin</title><p>{}</p><script>close()</script>'


def stopped_page(accept_language: str) -> HTMLResponse:
    """The page a stopped session's tab loads next, which closes the tab.

    A session page reloads to reconnect once its session ends, and lands
    here. A browser lets a tab that a script opened close itself, which the
    web app cannot do for a tab whose handle it lost (WebKit severs every tab
    its page opens) or whose origin differs from its own after giving it no
    opener (Chrome on a session's own origin). A tab that may not close
    itself shows the launcher's line for a stopped session, in the first
    language the browser accepts that the UI carries.
    """
    catalogs = {
        os.path.basename(name).split(".")[0]: name
        for name in glob.glob(os.path.join(settings.ui_path, "i18n", "*.json"))
    }
    wanted = [tag.split(";")[0].strip().split("-")[0].lower() for tag in accept_language.split(",")]
    text = ""
    for lang in (*wanted, "en"):
        if lang in catalogs:
            try:
                with open(catalogs[lang], encoding="utf-8") as handle:
                    text = json.load(handle)["options"]["status"]["sessionStopped"]
                break
            except (OSError, ValueError, KeyError):
                continue
    return HTMLResponse(STOPPED_PAGE.format(html.escape(text)), status_code=404)


async def _peer_upstream(session_id: str, request: Request) -> str | None:
    """Return the peer listener of the node that runs a session this node does not.

    Only a frontend proxies to another node, and never a request that
    already came over the peer listener, so no request is passed on twice.

    Raises:
        HTTPException: 503 while the node that runs the session is away.
    """
    if session_id in state.sessions or cluster.on_peer_listener(request):
        return None
    if "frontend" not in cluster.roles() or not cluster.is_clustered():
        return None
    node_id = await cluster.find_session(session_id)
    if not node_id or node_id == cluster.NODE_ID:
        return None
    if not cluster.is_approved(node_id) or not cluster.is_alive(node_id):
        # The session is not over: say so, rather than let its tab close itself.
        name = cluster.NODES.get(node_id, {}).get("name", node_id)
        raise HTTPException(status_code=503, detail=f"Node '{name}', which runs this session, is not answering.")
    return cluster.NODES[node_id]["address"]


@router.get("/route/{session_id}")
async def route(session_id: str, request: Request) -> Response:
    """Tell Caddy whether a request for a session belongs to another node.

    Returns:
        An empty 200 response, with `X-Upstream-Peer` when another node runs
        the session.
    """
    upstream = await _peer_upstream(session_id.lower(), request)
    return Response(status_code=200, headers={"X-Upstream-Peer": upstream} if upstream else {})


@router.get("/resolve_session/{session_id}")
async def resolve_session(session_id: str, request: Request) -> Response:
    """Authorise a proxied request and tell Caddy where to send it.

    Returns:
        An empty 200 response with `X-Upstream-Host` and `X-Upstream-Auth`,
        or with `X-Upstream-Peer` for a session another node runs, which
        authorises the request itself; for a page load of a session that is
        not running, `stopped_page`.

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
        upstream = await _peer_upstream(session_id, request)
        if upstream:
            return Response(status_code=200, headers={"X-Upstream-Peer": upstream})
        if request.headers.get("sec-fetch-dest") == "document":
            return stopped_page(request.headers.get("accept-language", ""))
        raise HTTPException(status_code=404, detail="Session not found.")

    is_standard_auth = token_matches(token, session.get("access_token"))
    is_collab_controller = False
    is_collab_viewer = False
    if session.get("is_collaboration"):
        is_collab_controller = token_matches(collab_token, session.get("controller_token"))
        is_collab_viewer = any(token_matches(collab_token, v["token"]) for v in session.get("viewers", []))
    if not (is_standard_auth or is_collab_controller or is_collab_viewer):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid session or token.")
    if bool(session.get("own_origin")) != on_session_origin(request, session_id):
        raise HTTPException(status_code=403, detail="Forbidden: the session is served from another origin.")

    headers = {"X-Upstream-Host": host_port(session["ip"], session["port"])}
    if "custom_user" in session and "password" in session:
        auth_b64 = base64.b64encode(f"{session['custom_user']}:{session['password']}".encode()).decode()
        headers["X-Upstream-Auth"] = f"Basic {auth_b64}"
    return Response(status_code=200, headers=headers)
