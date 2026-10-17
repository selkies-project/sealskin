"""Thumbnails of running sessions for the launcher's session cards.

A thumbnail is the desktop a session's container draws, asked of its Selkies
at `/<session id>/api/screenshot` (a PNG with the cursor drawn in), scaled
to `WIDTH` pixels across and handed to the client as a JPEG data URI inside
JSON, so it travels the encrypted lane and between nodes like any other
answer. The container is asked with the session's basic credentials, and in
a room, where Selkies runs in its secure mode, with the room's controller
token as `?token=` as well: the master token is taken on the `Authorization`
header alone, which the basic login in front already owns.

One capture serves every page that asks within `FRESH_SECONDS`, and pages
asking at once share the capture under way. A container that gives none, as
one without pixelflux screenshots or one whose desktop is not up yet, is not
asked again for `RETRY_SECONDS`. A page the user has just landed on asks
for a `fresh` capture, which is taken now unless one is under way: a
screenshot reads the frame back from the GPU, so it is taken when someone is
looking, and the launcher asks sparingly otherwise.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException
from PIL import Image

from .providers.base_provider import host_port
from .state import state

logger = logging.getLogger(__name__)

#: Width of a thumbnail in pixels; the cards show it at half that on a dense display.
WIDTH = 640
#: How long a thumbnail answers for before the container is asked again.
FRESH_SECONDS = 4.0
#: How long a container that gave no screenshot is left alone.
RETRY_SECONDS = 20.0
#: JPEG quality of a thumbnail.
QUALITY = 72
TIMEOUT = httpx.Timeout(6.0, connect=2.0)

# Session id to (taken at, thumbnail or None for a refusal).
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_INFLIGHT: dict[str, asyncio.Future[dict[str, Any] | None]] = {}


def _credentials(session: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return the headers and query that let this server into a session's container API."""
    headers: dict[str, str] = {}
    query: dict[str, str] = {}
    if session.get("custom_user") and session.get("password"):
        credentials = f"{session['custom_user']}:{session['password']}".encode()
        headers["Authorization"] = f"Basic {base64.b64encode(credentials).decode()}"
    if session.get("master_token"):
        if "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {session['master_token']}"
        elif session.get("controller_token"):
            # Behind the basic login, secure mode takes a session token in the query.
            query["token"] = session["controller_token"]
    return headers, query


def _shrink(png: bytes) -> dict[str, Any]:
    """Scale a PNG to `WIDTH` across and return it as a JPEG data URI with its size."""
    with Image.open(io.BytesIO(png)) as image:
        image.thumbnail((WIDTH, WIDTH * 4), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=QUALITY, optimize=True)
        width, height = image.size
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode(),
        "width": width,
        "height": height,
    }


async def _capture(session_id: str, session: dict[str, Any]) -> dict[str, Any] | None:
    """Ask the session's container for a screenshot and shrink it, or return `None` when it gives none."""
    url = f"http://{host_port(session['ip'], session['port'])}/{session_id}/api/screenshot"
    headers, query = _credentials(session)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            answer = await client.get(url, headers=headers, params=query)
    except httpx.HTTPError as exc:
        logger.debug("[%s] No screenshot: %s", session_id, exc)
        return None
    if answer.status_code != 200 or not answer.content:
        logger.debug("[%s] No screenshot: %s %s", session_id, answer.status_code, answer.text[:80])
        return None
    try:
        thumbnail = await asyncio.to_thread(_shrink, answer.content)
    except (OSError, ValueError) as exc:
        logger.warning("[%s] Screenshot could not be read: %s", session_id, exc)
        return None
    thumbnail["taken_at"] = time.time()
    return thumbnail


def _prune() -> None:
    """Drop what is kept for sessions that are gone."""
    for session_id in [sid for sid in _CACHE if sid not in state.sessions]:
        _CACHE.pop(session_id, None)


async def thumbnail(session_id: str, session: dict[str, Any], fresh: bool = False) -> dict[str, Any]:
    """Return a session's thumbnail, `{"image", "width", "height", "taken_at"}`.

    Args:
        session_id: The session.
        session: Its record, with the container's address and credentials.
        fresh: Take a new capture rather than answer with a recent one.

    Raises:
        HTTPException: 404 when the container gives no screenshot.
    """
    _prune()
    now = time.time()
    kept = _CACHE.get(session_id)
    if kept and not fresh and now - kept[0] < (FRESH_SECONDS if kept[1] else RETRY_SECONDS):
        result = kept[1]
    elif session_id in _INFLIGHT:
        result = await asyncio.shield(_INFLIGHT[session_id])
    else:
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        _INFLIGHT[session_id] = future
        try:
            result = await _capture(session_id, session)
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()
            raise
        finally:
            _INFLIGHT.pop(session_id, None)
        future.set_result(result)
        _CACHE[session_id] = (time.time(), result)
    if result is None:
        raise HTTPException(status_code=404, detail="The session has no screenshot to show yet.")
    return result
