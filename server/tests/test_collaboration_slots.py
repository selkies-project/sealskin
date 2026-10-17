"""Gamepad slots in a collaboration room: several per participant."""

import pytest

from app import collaboration
from app.state import state


@pytest.fixture
def room(monkeypatch):
    """A session with a controller and two viewers, and what its pushes said."""
    pushed = {"tables": [], "notices": []}

    async def push_tokens(session_id, session_data):
        pushed["tables"].append(collaboration.launch.collaboration_initial_tokens(session_data))

    async def to_room(session_id, payload):
        pushed["notices"].append(payload["message"])

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(collaboration, "broadcast_token_state", push_tokens)
    monkeypatch.setattr(collaboration, "broadcast_to_room", to_room)
    monkeypatch.setattr(collaboration, "broadcast_state", nothing)
    monkeypatch.setattr(collaboration.config_store, "save_sessions", nothing)
    state.sessions["s"] = {
        "controller_token": "c",
        "controller_slot": None,
        "viewers": [
            {"token": "v1", "username": "Ann", "slot": None},
            {"token": "v2", "username": "Bo", "slot": 2},
        ],
    }
    yield state.sessions["s"], pushed
    state.sessions.pop("s", None)


@pytest.mark.asyncio
async def test_assigning_adds_a_slot_beside_those_held(room):
    session, pushed = room
    await collaboration.handle_assign_slot("s", "v1", 4)
    await collaboration.handle_assign_slot("s", "v1", 3)
    assert session["viewers"][0]["slot"] == [3, 4]
    assert pushed["tables"][-1]["v1"]["slot"] == [3, 4]
    assert pushed["notices"][-1] == "Gamepad 3 was assigned to Ann."


@pytest.mark.asyncio
async def test_assigning_a_held_slot_takes_it_from_its_holder(room):
    session, pushed = room
    await collaboration.handle_assign_slot("s", "v1", 1)
    await collaboration.handle_assign_slot("s", "v1", 2)
    assert session["viewers"][0]["slot"] == [1, 2]
    assert session["viewers"][1]["slot"] is None
    assert pushed["notices"][-2:] == ["Bo was unassigned from Gamepad 2.", "Gamepad 2 was assigned to Ann."]


@pytest.mark.asyncio
async def test_releasing_a_slot_keeps_the_holders_others(room):
    session, pushed = room
    await collaboration.handle_assign_slot("s", "c", 1)
    await collaboration.handle_assign_slot("s", "c", 3)
    await collaboration.handle_release_slot("s", 1)
    assert session["controller_slot"] == 3
    assert pushed["tables"][-1]["c"]["slot"] == 3
    assert pushed["notices"][-1] == "Controller was unassigned from Gamepad 1."


@pytest.mark.asyncio
async def test_clearing_a_participant_frees_every_slot(room):
    session, pushed = room
    await collaboration.handle_assign_slot("s", "v2", 4)
    await collaboration.handle_assign_slot("s", "v2", None)
    assert session["viewers"][1]["slot"] is None
    assert pushed["notices"][-1] == "Bo was unassigned from Gamepads 2 and 4."


@pytest.mark.asyncio
async def test_slots_outside_the_container_are_ignored(room):
    session, pushed = room
    for slot in (0, 5, "3", True, [1, 2]):
        await collaboration.handle_assign_slot("s", "v1", slot)
        await collaboration.handle_release_slot("s", slot)
    assert session["viewers"][0]["slot"] is None and session["viewers"][1]["slot"] == 2
    assert pushed["tables"] == []


def test_gamepads_text():
    assert collaboration.gamepads_text([3]) == "Gamepad 3"
    assert collaboration.gamepads_text([3, 4]) == "Gamepads 3 and 4"
    assert collaboration.gamepads_text([1, 2, 4]) == "Gamepads 1, 2 and 4"
