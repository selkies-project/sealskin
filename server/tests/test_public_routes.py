"""Anonymous routes: public share downloads and the session proxy's `forward_auth`."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import internal, sessions, shares
from app.state import state

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SESSION_ID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.fixture
def http():
    app = FastAPI()
    app.include_router(shares.public_router)
    app.include_router(internal.router)
    app.include_router(sessions.proxy_router)
    yield TestClient(app, raise_server_exceptions=False)
    state.public_shares.clear()
    state.download_tokens.clear()


def test_forward_auth_accepts_each_token_and_nothing_else(http):
    state.sessions[SESSION_ID] = {
        "ip": "10.0.0.2",
        "port": 3000,
        "access_token": "access",
        "is_collaboration": True,
        "controller_token": "controller",
        "viewers": [{"token": "viewer"}],
    }
    url = f"/internal/resolve_session/{SESSION_ID}"
    for query in ({"access_token": "access"}, {"token": "controller"}, {"token": "viewer"}):
        assert http.get(url, params=query).status_code == 200, query
    for query in ({}, {"access_token": "wrong"}, {"token": "wrong"}, {"access_token": "é"}, {"token": "é"}):
        assert http.get(url, params=query).status_code == 403, query
    for token in ("wrong", "é"):
        assert http.get(f"/{SESSION_ID}/", params={"access_token": token}).status_code == 403, token
