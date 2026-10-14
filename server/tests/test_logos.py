"""The pictures of applications and links are served by the server, never loaded from their origin."""

import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import branding, config_store, logos
from app.models import Branding, InstalledAppRecord
from app.routers import applications
from app.state import state

PNG = b"\x89PNG\r\n\x1a\n picture"


@pytest.fixture
def web(monkeypatch):
    """The web answering the logo fetches, counting them."""
    calls = []

    def handle(request):
        calls.append(str(request.url))
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if request.url.path.endswith(".html"):
            return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    monkeypatch.setattr(logos, "_transport", httpx.MockTransport(handle))
    logos.reset()
    yield calls
    logos.reset()


@pytest.fixture
def http():
    app = FastAPI()
    app.include_router(applications.public_router)
    return TestClient(app)


def test_a_web_address_becomes_a_name_and_other_values_stay(web):
    served = logos.local("https://example.invalid/firefox.png")
    assert served.startswith("/api/logos/") and served == logos.local("https://example.invalid/firefox.png")
    assert logos.local("/api/app_icon/meta") == "/api/app_icon/meta"
    assert logos.local("") == ""


def test_a_picture_is_fetched_once_and_served_with_its_type(web, http):
    name = logos.local("https://example.invalid/firefox.png").rsplit("/", 1)[1]
    first = http.get(f"/api/logos/{name}")
    assert first.status_code == 200 and first.content == PNG
    assert first.headers["content-type"] == "image/png"
    assert "default-src 'none'" in first.headers["content-security-policy"]
    again = http.get(f"/api/logos/{name}", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert len(web) == 1


def test_only_pictures_the_server_handed_out_are_served(web, http):
    assert http.get(f"/api/logos/{logos.name_of('https://example.invalid/never.png')}").status_code == 404
    assert http.get("/api/logos/../etc/passwd").status_code == 404
    page = logos.local("https://example.invalid/page.html").rsplit("/", 1)[1]
    assert http.get(f"/api/logos/{page}").status_code == 404
    assert web == ["https://example.invalid/page.html"]


async def test_a_refresh_prefetches_the_stores_pictures_and_prunes_the_rest(web, store_with_firefox):
    stale = logos._dir()
    stale_file = f"{stale}/{'0' * 32}.png"
    os.makedirs(stale, exist_ok=True)
    open(stale_file, "wb").close()
    await config_store.refresh_store_caches()
    assert web == ["https://example.invalid/firefox.png"]
    assert logos._find(logos.name_of("https://example.invalid/firefox.png"))
    assert not os.path.exists(stale_file)


def test_apps_sessions_and_links_hand_out_the_served_address(web, store_with_firefox):
    served = logos.local("https://example.invalid/firefox.png")
    assert state.store_entries["Test Store"]["firefox"]["logo"] == served
    record = InstalledAppRecord(
        id="app-1", source="Test Store", source_app_id="firefox", app_template="Default", users=["all"],
        overrides={"logo": "https://example.invalid/firefox.png"},
    )
    assert config_store.resolve_app(record).logo == served
    assert config_store.apply_partial_update(record, {"logo": "https://example.invalid/firefox.png"}).overrides == {}
    state.branding = Branding(links=[{"name": "Wiki", "url": "https://wiki", "icon": "https://example.invalid/wiki.png"}, {"name": "Docs", "url": "https://docs", "icon": "fa-book"}])
    links = branding.view().links
    assert links[0].icon == "https://example.invalid/wiki.png" and links[0].image.startswith("/api/logos/")
    assert links[1].image == ""
    assert Branding.model_validate(branding.view().model_dump()).links[0].icon == "https://example.invalid/wiki.png"
