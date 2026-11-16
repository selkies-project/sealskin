"""Whether anyone has a session open, for the limit that ends one left alone.

A session's Selkies lists the pages connected to it at
`/<session id>/api/sessions`, asked with the credentials a thumbnail is asked
with (`screenshots.credentials`). Every container of the session is asked, so
a room counts whichever application its pages are on, viewers included.

`watch` keeps, for each session it is given, the last time a page was
connected. A session it sees for the first time counts as connected then, so
a restart of the server starts every clock again; a container that does not
answer, or whose Selkies has no such list, counts as connected too, so a
session is ended only on a container's own word that nobody is there.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .providers.base_provider import host_port
from .screenshots import credentials
from .state import state

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(4.0, connect=2.0)

# Session id to the last time a page was connected to it.
_SEEN: dict[str, float] = {}


async def _pages(client: httpx.AsyncClient, session_id: str, session: dict[str, Any], container: dict[str, Any]) -> int | None:
    """Return how many pages a container has connected, or `None` when it does not say."""
    url = f"http://{host_port(container['ip'], container['port'])}/{session_id}/api/sessions"
    headers, query = credentials(session)
    try:
        answer = await client.get(url, headers=headers, params=query)
        pages = answer.json()["sessions"] if answer.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.debug("[%s] No list of connected pages: %s", session_id, exc)
        return None
    return len(pages) if isinstance(pages, list) else None


async def connected(session_id: str, session: dict[str, Any]) -> bool:
    """Whether a page is connected to a session, or one of its containers could not say."""
    containers = [c for c in (session.get("container_registry") or {}).values() if c.get("ip") and c.get("port")]
    if not containers and session.get("ip") and session.get("port"):
        containers = [session]
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        counts = await asyncio.gather(*(_pages(client, session_id, session, c) for c in containers))
    return not counts or any(count is None or count > 0 for count in counts)


async def watch(session_ids: list[str]) -> None:
    """Ask the given sessions of this node who is connected, and forget every other session."""
    now = time.time()
    watched = {sid: state.sessions[sid] for sid in session_ids if sid in state.sessions}
    for session_id in [sid for sid in _SEEN if sid not in watched]:
        del _SEEN[session_id]
    for session_id in watched:
        _SEEN.setdefault(session_id, now)
    answers = await asyncio.gather(*(connected(sid, data) for sid, data in watched.items()), return_exceptions=True)
    for session_id, answer in zip(watched, answers, strict=True):
        if answer is not False:
            _SEEN[session_id] = time.time()


def alone_seconds(session_id: str) -> float:
    """Return how long a watched session has had no page connected; 0 for one not watched."""
    seen = _SEEN.get(session_id)
    return max(0.0, time.time() - seen) if seen else 0.0
