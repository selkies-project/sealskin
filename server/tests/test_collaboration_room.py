"""The collaboration room page, the Selkies session its iframe opens, and the apps a controller swaps to."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import collaboration
from app.settings import settings
from app.state import state

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SESSION_ID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def http(tmp_path, monkeypatch):
    (tmp_path / "room").mkdir()
    (tmp_path / "room" / "room.html").write_text(
        '<iframe data-src="{{IFRAME_SRC}}"></iframe><!-- CLIENT_DATA -->', encoding="utf-8"
    )
    monkeypatch.setattr(settings, "ui_path", str(tmp_path))
    app = FastAPI()
    app.include_router(collaboration.router)
    yield TestClient(app, base_url="https://testserver")
    state.sessions.pop(SESSION_ID, None)


def test_the_room_opens_selkies_with_the_token_in_the_query_and_the_fragment(http):
    state.sessions[SESSION_ID] = {
        "is_collaboration": True,
        "access_token": "access",
        "controller_token": "controller",
        "participant_invite_token": "participant-invite",
        "readonly_invite_token": "readonly-invite",
        "viewers": [{"token": "viewer", "permission": "participant"}],
    }
    for token in ("controller", "viewer"):
        page = http.get(f"/room/{SESSION_ID}", params={"token": token})
        assert page.status_code == 200, token
        assert f'data-src="/{SESSION_ID}/?token={token}#token={token}"' in page.text, token

        cookie = page.headers["set-cookie"]
        assert f"collab_token_{SESSION_ID}={token}" in cookie and f"Path=/{SESSION_ID}" in cookie, cookie


def test_the_room_pages_assets_come_through_the_companion_lane():
    """A guest with an invite link has no account at a proxy's provider: the assets take the lane a proxy passes raw."""
    from app import collaboration

    page = collaboration._absolutize_room_assets('<link href="room.css"><script src="room.js"></script><a href="/room/x">x</a><img src="../icons/i.png">')
    assert 'href="/companion/ui/room/room.css"' in page and 'src="/companion/ui/room/room.js"' in page
    assert 'src="/companion/ui/icons/i.png"' in page and 'href="/room/x"' in page


def test_a_room_controller_swaps_only_to_apps_open_to_the_sessions_owner(http, monkeypatch):
    from app.models import InstalledApp

    started = []

    async def ensure(session_id, app_id, timezone=None):
        started.append(app_id)
        return {"instance_id": "i", "ip": "127.0.0.1", "port": 1}

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(collaboration.launch, "ensure_container_for_session", ensure)
    monkeypatch.setattr(collaboration.config_store, "save_sessions", nothing)
    monkeypatch.setattr(collaboration, "broadcast_token_state", nothing)
    for app_id, users in (("open", ["all"]), ("bobs", ["bob"])):
        state.installed_apps[app_id] = InstalledApp.model_construct(
            id=app_id, name=app_id, logo="", users=users, groups=[], provider_config=None
        )
    state.sessions[SESSION_ID] = {
        "is_collaboration": True, "controller_token": "ctrl", "viewers": [], "username": "alice",
        "provider_app_id": "other", "container_registry": {},
    }
    try:
        with http.websocket_connect(f"/ws/room/{SESSION_ID}?token=ctrl") as ws:
            ws.send_json({"action": "swap_app", "app_id": "bobs"})
            ws.send_json({"action": "swap_app", "app_id": "open"})
            refused = None
            for _ in range(20):
                message = ws.receive_json()
                if message.get("type") == "error":
                    refused = message
                if message.get("type") == "app_swapped" and message.get("app_name") == "open":
                    break
        assert refused and "not open" in refused["message"]
        assert started == ["open"]
    finally:
        for app_id in ("open", "bobs"):
            state.installed_apps.pop(app_id, None)
