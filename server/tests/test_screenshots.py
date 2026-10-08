"""Session thumbnails: how the container is asked, what is kept, and who may ask."""

from __future__ import annotations

import io
import time

import httpx
import pytest
from PIL import Image

from app import screenshots
from app.state import state
from tests.test_cluster import WEB, node  # noqa: F401

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SESSION = "11111111-2222-4333-8444-555555555555"
# Patching the module's client patches httpx's own, so the real class is kept here.
REAL_CLIENT = httpx.AsyncClient


def _png(width=1600, height=900):
    out = io.BytesIO()
    Image.new("RGB", (width, height), (40, 80, 120)).save(out, "PNG")
    return out.getvalue()


def _container(monkeypatch, handler):
    """Have the module's HTTP client answer with `handler`, and return the requests it saw."""
    seen = []

    def respond(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(screenshots.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(respond), **kw))
    screenshots._CACHE.clear()
    return seen


async def test_a_thumbnail_is_the_shrunk_screenshot_asked_with_the_sessions_credentials(monkeypatch):
    seen = _container(monkeypatch, lambda _r: httpx.Response(200, content=_png(), headers={"content-type": "image/png"}))
    session = {"ip": "10.0.0.5", "port": 3000, "custom_user": "u", "password": "p", "master_token": "m", "controller_token": "c"}
    state.sessions[SESSION] = session
    first = await screenshots.thumbnail(SESSION, session)
    assert first["image"].startswith("data:image/jpeg;base64,") and (first["width"], first["height"]) == (640, 360)
    # In a room the basic login owns the Authorization header, so the controller token rides the query.
    assert seen[0].url == f"http://10.0.0.5:3000/{SESSION}/api/screenshot?token=c"
    assert seen[0].headers["Authorization"].startswith("Basic ")
    # Within the fresh window every page gets the same capture, unless it asks for a fresh one.
    assert await screenshots.thumbnail(SESSION, session) is first and len(seen) == 1
    assert (await screenshots.thumbnail(SESSION, session, fresh=True)) is not first and len(seen) == 2
    screenshots._CACHE[SESSION] = (time.time() - screenshots.FRESH_SECONDS - 1, first)
    assert (await screenshots.thumbnail(SESSION, session)) is not first and len(seen) == 3
    # Outside a room the basic login is the only credential; without one, the master token is the bearer.
    del session["master_token"]
    screenshots._CACHE.clear()
    await screenshots.thumbnail(SESSION, session)
    assert "token" not in str(seen[3].url)
    screenshots._CACHE.clear()
    await screenshots.thumbnail(SESSION, {**session, "custom_user": "", "master_token": "m"})
    assert seen[4].headers["Authorization"] == "Bearer m" and "token" not in str(seen[4].url)


async def test_a_container_without_screenshots_is_left_alone_for_a_while(monkeypatch):
    seen = _container(monkeypatch, lambda _r: httpx.Response(501, text="pixelflux without screenshots"))
    session = {"ip": "10.0.0.5", "port": 3000, "custom_user": "u", "password": "p"}
    state.sessions[SESSION] = session
    for _ in range(2):
        with pytest.raises(Exception, match="no screenshot"):
            await screenshots.thumbnail(SESSION, session)
    assert len(seen) == 1
    # What is kept for a session that ended goes with it.
    del state.sessions[SESSION]
    _container(monkeypatch, lambda _r: httpx.Response(200, content=_png(320, 200), headers={"content-type": "image/png"}))
    state.sessions[SESSION] = session
    assert (await screenshots.thumbnail(SESSION, session))["width"] == 320


def test_only_the_owner_is_shown_a_sessions_thumbnail(node, monkeypatch):  # noqa: F811
    _container(monkeypatch, lambda _r: httpx.Response(200, content=_png(800, 600), headers={"content-type": "image/png"}))
    signed_in = node.post("/api/auth/root", json={"token": "a-root-token-for-the-tests"}, headers=WEB)
    cookie = {**WEB, "Cookie": signed_in.headers["set-cookie"].split(";", 1)[0]}
    state.sessions[SESSION] = {"username": "root", "ip": "10.0.0.5", "port": 3000, "custom_user": "u", "password": "p"}
    other = SESSION.replace("1111", "9999")
    state.sessions[other] = {"username": "alice", "ip": "10.0.0.6", "port": 3000}
    answer = node.get(f"/api/sessions/{SESSION}/screenshot", headers=cookie)
    assert answer.status_code == 200 and answer.json()["height"] == 480
    assert node.get(f"/api/sessions/{SESSION}/screenshot?fresh=1", headers=cookie).status_code == 200
    assert node.get(f"/api/sessions/{other}/screenshot", headers=cookie).status_code == 404
    assert node.get(f"/api/sessions/{SESSION}/screenshot").status_code in (400, 401)
