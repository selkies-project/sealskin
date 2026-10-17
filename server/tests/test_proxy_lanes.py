"""What the proxy lets through and what the API takes from it: the internal routes, the cookies, and the frames."""

import os
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import internal
from tests.conftest import PROXY_SECRET

SID = "3487533d-0e2d-47e1-a1a9-9620aee90512"
TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "Caddyfile.tpl")


def _block(text, name):
    """Return the body of the named Caddyfile snippet."""
    return text.split(f"({name}) {{", 1)[1].split("\n}\n", 1)[0]


@pytest.fixture
def caddyfile():
    with open(TEMPLATE, encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.parametrize("headers,status", [({}, 403), ({"X-SealSkin-Secret": "not-it"}, 403), ({"X-SealSkin-Secret": PROXY_SECRET}, 200)])
def test_the_internal_routes_answer_the_proxys_sub_requests_alone(headers, status):
    app = FastAPI()
    app.include_router(internal.router)
    # The browser's address, as uvicorn reports it from the sub-request's X-Forwarded-For.
    client = TestClient(app, client=("203.0.113.9", 50000), headers=headers)
    assert client.get(f"/internal/route/{SID}").status_code == status
    assert client.get(f"/internal/resolve_session/{SID}").status_code == (404 if status == 200 else 403)


def test_the_container_gets_no_cookie_but_the_token_cookie_of_its_own_client(caddyfile):
    container = _block(caddyfile, "to_container")
    found = re.search(r'header_up Cookie "([^"]+)" "\$1"', container)
    assert found, container
    keep = re.compile(found.group(1))
    # The sign-in cookie and a proxy's own stay with the node; Selkies' `selkies_token` goes on as it was sent.
    assert keep.sub(r"\1", "__Host-sealskin=abc; authentik_session=def") == ""
    assert keep.sub(r"\1", f"sealskin_session_token_{SID}=tok; collab_token_{SID}=c") == ""
    assert keep.sub(r"\1", "__Host-sealskin=abc; selkies_token=t%3D1; collab_token_x=c") == "selkies_token=t%3D1"
    assert keep.sub(r"\1", "selkies_token=t; a=1") == "selkies_token=t"
    assert keep.sub(r"\1", "xselkies_token=no; selkies_token=yes") == "selkies_token=yes"
    # The node that runs a session resolves it from its cookies itself, so the peer gets them all.
    assert "Cookie" not in _block(caddyfile, "to_peer")


def test_the_served_session_pages_are_framed_by_this_origin_and_the_shells_alone(caddyfile):
    routes = _block(caddyfile, "session_routes")
    served = routes.split("handle @served {", 1)[1]
    files = served.split("file_server")
    assert len(files) == 3
    for handle in files[:2]:
        policy = handle.rsplit("header {", 1)[1]
        assert "Content-Security-Policy \"frame-ancestors 'self' " in policy and "defer" in policy, handle


def test_the_apps_own_name_passes_the_preflight_of_another_origin(caddyfile):
    allowed = re.search(r'Access-Control-Allow-Headers "([^"]+)"', _block(caddyfile, "entrance")).group(1)
    assert "X-SealSkin-App" in [h.strip() for h in allowed.split(",")]
