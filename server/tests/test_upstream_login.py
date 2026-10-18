"""Which login the proxy hop to a session's container carries, by who asked for it.

The session's owner and a collaboration room's controller reach the container as the
session's Basic login. A room's viewer reaches it as the view-only login, which Selkies
caps at the viewer role, and never as the controller's, which would let it upload, read
files, and drive input by leaving its Selkies token off a request. A session launched
before it had a view-only login gives a viewer none; its Selkies token holds it then.
"""

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import launch
from app.routers import internal
from app.state import state
from tests.conftest import PROXY_SECRET

INTERNAL = {"X-SealSkin-Secret": PROXY_SECRET}
SID = "5c1b3f0e-8a7d-4f2b-9c61-2f1e0b9d7a44"


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(internal.router)
    state.sessions.clear()
    yield TestClient(app, base_url="https://sealskin.example:8443", client=("127.0.0.1", 50000))
    state.sessions.clear()


def room(**extra):
    state.sessions[SID] = {
        "access_token": "own", "ip": "10.0.0.2", "port": 3000, "custom_user": "u", "password": "p",
        "is_collaboration": True, "controller_token": "ctl", "viewers": [{"token": "view"}],
        "own_origin": False, **extra,
    }


def login(client, query):
    resp = client.get(f"/internal/resolve_session/{SID}?{query}", headers=INTERNAL)
    assert resp.status_code == 200, resp.text
    auth = resp.headers.get("X-Upstream-Auth")
    return base64.b64decode(auth.split()[1]).decode() if auth else None


def test_the_owner_and_the_controller_reach_the_container_as_the_session(client):
    room(viewonly_password="v")
    assert login(client, "access_token=own") == "u:p"
    assert login(client, "token=ctl") == "u:p"


def test_a_viewer_reaches_it_as_the_view_only_login(client):
    room(viewonly_password="v")
    assert login(client, "token=view") == "u:v"


def test_a_viewer_of_a_session_without_one_gets_no_login(client):
    room()
    assert login(client, "token=view") is None


def test_every_session_is_launched_with_a_view_only_login():
    env = launch.session_base_env("s", "u", "p", "m", None, "v")
    assert env["VIEWONLY_PASSWORD"] == "v" and env["PASSWORD"] == "p"
    assert "VIEWONLY_PASSWORD" in launch.RESERVED_ENV
