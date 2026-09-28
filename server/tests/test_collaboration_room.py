"""The collaboration room page and the Selkies session its iframe opens."""

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


def test_the_room_opens_selkies_with_the_token_in_the_fragment(http):
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
        assert f'data-src="/{SESSION_ID}/#token={token}"' in page.text, token
        assert f"/{SESSION_ID}/?token=" not in page.text, token
        cookie = page.headers["set-cookie"]
        assert f"collab_token_{SESSION_ID}={token}" in cookie and f"Path=/{SESSION_ID}" in cookie, cookie
