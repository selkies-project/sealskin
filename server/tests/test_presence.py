"""The limit that ends a session nobody is connected to."""

import time

import httpx

from app import presence, quota, user_manager
from app.state import state

REAL_CLIENT = httpx.AsyncClient


def _selkies(monkeypatch, answers):
    """Answer `/api/sessions` per container address from `answers`, a status code or a list of pages."""
    asked = []

    def respond(request):
        asked.append(request)
        answer = answers[request.url.host]
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json={"sessions": answer})

    monkeypatch.setattr(presence.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(presence, "_SEEN", {})
    return asked


def _session(ip="10.0.0.1"):
    return {"ip": ip, "port": 3000, "custom_user": "u", "password": "p", "username": "ann", "created_at": time.time()}


async def test_a_session_is_alone_from_the_pass_that_finds_no_page(monkeypatch):
    answers = {"10.0.0.1": [{"id": "1", "role": "controller"}]}
    asked = _selkies(monkeypatch, answers)
    state.sessions["s"] = _session()
    await presence.watch(["s"])
    assert asked[0].url.path == "/s/api/sessions" and asked[0].headers["authorization"].startswith("Basic ")
    presence._SEEN["s"] -= 600
    await presence.watch(["s"])
    assert presence.alone_seconds("s") < 5
    answers["10.0.0.1"] = []
    presence._SEEN["s"] -= 600
    await presence.watch(["s"])
    assert presence.alone_seconds("s") >= 600
    await presence.watch([])
    assert presence.alone_seconds("s") == 0


async def test_a_container_that_does_not_say_counts_as_connected(monkeypatch):
    answers = {"10.0.0.1": 404, "10.0.0.2": []}
    _selkies(monkeypatch, answers)
    state.sessions["s"] = {**_session(), "container_registry": {"a": {"ip": "10.0.0.1", "port": 3000}, "b": {"ip": "10.0.0.2", "port": 3000}}}
    await presence.watch(["s"])
    presence._SEEN["s"] -= 600
    await presence.watch(["s"])
    assert presence.alone_seconds("s") < 5
    answers["10.0.0.1"] = []
    presence._SEEN["s"] -= 600
    await presence.watch(["s"])
    assert presence.alone_seconds("s") >= 600


def test_only_a_session_alone_past_its_users_limit_is_overdue(monkeypatch):
    limits = {"ann": {"session_idle_minutes": 5}, "bob": {"session_idle_minutes": -1}, "root": {"session_idle_minutes": 5, "admin": True}}
    monkeypatch.setattr(user_manager, "get_user", lambda name: {"username": name, "is_admin": False})
    monkeypatch.setattr(user_manager, "get_effective_settings", lambda name, groups=(): limits[name])
    for name in limits:
        state.sessions[name] = {**_session(), "username": name}
    assert quota.watched() == ["ann"]
    monkeypatch.setattr(presence, "_SEEN", {"ann": time.time() - 200})
    assert quota.overdue() == []
    presence._SEEN["ann"] -= 200
    assert quota.overdue() == [("ann", "nobody was connected to it for 5 minute(s)")]
