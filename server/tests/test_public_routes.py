"""Anonymous routes: public share downloads and the session proxy's `forward_auth`."""

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import PublicShareMetadata
from app.routers import internal, sessions, shares
from app.security import hash_share_password
from app.settings import settings
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


def _share(share_id, password):
    os.makedirs(settings.public_storage_path, exist_ok=True)
    with open(os.path.join(settings.public_storage_path, share_id), "w", encoding="utf-8") as handle:
        handle.write("shared")
    state.public_shares[share_id] = PublicShareMetadata(
        owner_username="owner",
        original_filename="f.txt",
        created_at=time.time(),
        size_bytes=6,
        password_hash=hash_share_password(password),
    )
    return state.public_shares[share_id]


def test_a_download_token_ends_with_its_share(http):
    share = _share("s1", "right")
    issued = http.post("/public/s1", data={"password": "right"}, follow_redirects=False)
    assert issued.status_code == 303
    share.expiry_timestamp = time.time() - 1
    assert http.get(issued.headers["location"]).status_code == 410


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
