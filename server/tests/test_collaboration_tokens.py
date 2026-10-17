"""A collaboration room's state reaches each viewer without another's token.

A room token lets whoever holds it take that participant's part, the
controller's taking the whole session, so the state, resolution, and speaker
pushes tell a viewer its own token and an alias for every other; the
controller, which acts on them, is told them all.
"""

import json

import pytest
from starlette.websockets import WebSocketState

from app import collaboration
from app.state import state


class FakeSocket:
    client_state = WebSocketState.CONNECTED

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(json.dumps(payload))


@pytest.fixture
def room():
    sockets = {"c": FakeSocket(), "v1": FakeSocket(), "v2": FakeSocket()}
    state.sessions["s"] = {
        "controller_token": "ctrl-secret",
        "controller_slot": None,
        "designated_speaker": "viewer-two-secret",
        "viewers": [
            {"token": "viewer-one-secret", "username": "Ann", "slot": None, "permission": "readonly"},
            {"token": "viewer-two-secret", "username": "Bo", "slot": 2, "permission": "full"},
        ],
    }
    collaboration.ROOM_CONNECTIONS["s"] = {
        "controller": {"websocket": sockets["c"], "public_id": "aa"},
        "viewers": {
            "viewer-one-secret": {"websocket": sockets["v1"], "public_id": "bb"},
            "viewer-two-secret": {"websocket": sockets["v2"], "public_id": "cc"},
        },
    }
    yield sockets
    collaboration.ROOM_CONNECTIONS.pop("s", None)
    collaboration.ROOM_ALIAS_KEYS.pop("s", None)
    state.sessions.pop("s", None)


@pytest.mark.asyncio
async def test_a_viewer_is_told_its_own_token_and_no_other(room):
    await collaboration.broadcast_state("s")
    await collaboration.broadcast_state("s")
    await collaboration.broadcast_to_room(
        "s", {"type": "resolution_update", "token": "viewer-two-secret", "width": 800, "height": 600}
    )
    own = {"v1": "viewer-one-secret", "v2": "viewer-two-secret"}
    for name, token in own.items():
        seen = "".join(room[name].sent)
        others = [t for t in ("ctrl-secret", *own.values()) if t != token]
        assert token in seen
        assert not any(t in seen for t in others), name
    one = [json.loads(m) for m in room["v1"].sent]
    assert one[0] == one[1], "aliases hold across pushes"
    assert one[0]["designated_speaker"] == one[2]["token"] == one[0]["viewers"][2]["token"]
    controller = "".join(room["c"].sent)
    assert all(t in controller for t in ("ctrl-secret", *own.values()))
