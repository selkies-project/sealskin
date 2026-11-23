"""Collaboration profiles: how several bind one room, and what a room under one refuses."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import collaboration, collaboration_profiles
from app.models import CollaborationProfile
from app.settings import settings
from app.state import state

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SESSION_ID = "123e4567-e89b-12d3-a456-426614174001"


def profile(**fields):
    return CollaborationProfile(id=fields.pop("id", "p"), name=fields.pop("name", "p"), **fields)


def test_several_profiles_bind_each_feature_at_its_most_restrictive():
    rules = collaboration_profiles.merge([
        profile(chat=False, max_viewers=8),
        profile(gamepads=False, resolution="controller", max_viewers=3),
        profile(max_viewers=-1),
    ])
    assert rules["chat"] is False and rules["gamepads"] is False and rules["webcams"] is True
    assert rules["resolution"] == "controller"
    assert rules["max_viewers"] == 3
    assert collaboration_profiles.merge([]) == collaboration_profiles.DEFAULT_RULES


def test_a_room_keeps_one_kind_of_invite():
    with pytest.raises(ValidationError):
        profile(participant_invites=False, readonly_invites=False)
    rules = collaboration_profiles.merge([profile(participant_invites=False), profile(readonly_invites=False)])
    assert rules["participant_invites"] is False and rules["readonly_invites"] is True


def test_the_rules_gate_the_actions_a_room_takes():
    rules = dict(collaboration_profiles.DEFAULT_RULES, app_switcher=False, input_handoff=False, resolution="controller")
    for action in ("swap_app", "get_apps", "assign_mk", "client_resolution", "request_resolutions"):
        assert not collaboration._action_allowed(rules, action), action
    for action in ("send_chat_message", "assign_slot", "set_username"):
        assert collaboration._action_allowed(rules, action), action


@pytest.fixture
def http(tmp_path, monkeypatch):
    (tmp_path / "room").mkdir()
    (tmp_path / "room" / "room.html").write_text("<!-- CLIENT_DATA -->", encoding="utf-8")
    monkeypatch.setattr(settings, "ui_path", str(tmp_path))
    app = FastAPI()
    app.include_router(collaboration.router)
    yield TestClient(app, base_url="https://testserver")
    state.sessions.pop(SESSION_ID, None)


def test_an_invite_the_rules_turn_off_admits_no_one(http, monkeypatch):
    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(collaboration, "broadcast_token_state", nothing)
    monkeypatch.setattr(collaboration.config_store, "save_sessions", nothing)
    state.sessions[SESSION_ID] = {
        "is_collaboration": True,
        "access_token": "access",
        "controller_token": "controller",
        "participant_invite_token": "participant-invite",
        "readonly_invite_token": "readonly-invite",
        "viewers": [],
        "collaboration_rules": dict(collaboration_profiles.DEFAULT_RULES, participant_invites=False),
    }
    assert http.get(f"/room/{SESSION_ID}", params={"token": "participant-invite"}).status_code == 401
    page = http.get(f"/room/{SESSION_ID}", params={"token": "controller"})
    assert "participantJoinUrl" not in page.text and "readonlyJoinUrl" in page.text
    joined = http.get(f"/room/{SESSION_ID}", params={"token": "readonly-invite"}, follow_redirects=False)
    assert joined.status_code in (302, 303, 307)


def test_a_full_room_turns_another_viewer_away_and_holds_back_their_stream(http, monkeypatch):
    pushed = []

    async def push(_session_id, session_data):
        pushed.append(set(collaboration.launch.collaboration_initial_tokens(session_data)))

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(collaboration, "broadcast_token_state", push)
    monkeypatch.setattr(collaboration.config_store, "save_sessions", nothing)
    state.sessions[SESSION_ID] = {
        "is_collaboration": True,
        "controller_token": "controller",
        "viewers": [
            {"token": "first", "permission": "participant", "username": "A"},
            {"token": "second", "permission": "readonly", "username": "B"},
        ],
        "collaboration_rules": dict(collaboration_profiles.DEFAULT_RULES, max_viewers=1),
    }
    with http.websocket_connect(f"/ws/room/{SESSION_ID}?token=first") as first:
        first.receive_json()
        with http.websocket_connect(f"/ws/room/{SESSION_ID}?token=second") as second:
            assert second.receive_json() == {"type": "room_full"}
    assert pushed and "second" not in pushed[0] and "first" in pushed[0]


def test_a_room_is_not_started_under_a_profile_no_longer_kept():
    with pytest.raises(collaboration_profiles.ProfileError):
        collaboration_profiles.rules_for({"collaboration_profiles": ["gone"]})
