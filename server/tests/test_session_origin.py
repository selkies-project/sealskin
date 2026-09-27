"""A session is served from the first origin its token is exchanged on, its own or the shared one."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import internal, sessions
from app.settings import settings
from app.state import state

SID = "3487533d-0e2d-47e1-a1a9-9620aee90512"
SHARED = "https://sealskin.example:8443"
OWN = f"https://{SID}.sealskin.example:8443"


@pytest.fixture
def app_client():
    app = FastAPI()
    app.include_router(sessions.proxy_router)
    app.include_router(internal.router)
    state.sessions.clear()
    state.sessions[SID] = {"access_token": "tok", "ip": "10.0.0.2", "port": 3000, "custom_user": "u", "password": "p"}
    yield lambda base: TestClient(
        app, base_url=base, follow_redirects=False, cookies={f"{settings.session_cookie_name}_{SID}": "tok"}
    )
    state.sessions.clear()


def exchange(client):
    return client.get(f"/{SID}/?access_token=tok")


def resolve(client):
    return client.get(f"/internal/resolve_session/{SID}")


@pytest.mark.parametrize("first,other", [(OWN, SHARED), (SHARED, OWN)])
def test_the_first_exchange_settles_the_origin(app_client, first, other):
    assert exchange(app_client(first)).status_code == 303
    assert state.sessions[SID]["own_origin"] is (first == OWN)
    assert resolve(app_client(first)).status_code == 200
    assert exchange(app_client(other)).status_code == 403
    assert resolve(app_client(other)).status_code == 403


def test_the_cookie_is_scoped_to_the_origin_it_was_set_on(app_client):
    cookie = exchange(app_client(OWN)).headers["set-cookie"]
    assert f"{settings.session_cookie_name}_{SID}=tok" in cookie
    assert "domain" not in cookie.lower()


def test_a_room_stays_on_the_shared_origin(app_client):
    state.sessions[SID]["is_collaboration"] = True
    assert exchange(app_client(OWN)).status_code == 403
    assert "own_origin" not in state.sessions[SID]
    assert exchange(app_client(SHARED)).status_code == 303
    assert state.sessions[SID]["own_origin"] is False


def test_a_stopped_session_page_closes_its_tab(app_client, tmp_path, monkeypatch):
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "en.0000.json").write_text('{"options": {"status": {"sessionStopped": "Stopped."}}}')
    (tmp_path / "i18n" / "de.1111.json").write_text('{"options": {"status": {"sessionStopped": "Beendet <b>."}}}')
    monkeypatch.setattr(settings, "ui_path", str(tmp_path))
    state.sessions.clear()
    client = app_client(SHARED)
    page = client.get(f"/internal/resolve_session/{SID}", headers={"Sec-Fetch-Dest": "document", "Accept-Language": "fr, de-DE;q=0.8"})
    assert page.status_code == 404
    assert "<script>close()</script>" in page.text and "Beendet &lt;b&gt;." in page.text
    fallback = client.get(f"/internal/resolve_session/{SID}", headers={"Sec-Fetch-Dest": "document", "Accept-Language": "../../x"})
    assert "<p>Stopped.</p>" in fallback.text
    assert client.get(f"/internal/resolve_session/{SID}").json() == {"detail": "Session not found."}
