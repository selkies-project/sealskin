"""The address of an application: its page, its manifest, and its icon."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import InstalledApp
from app.routers import entry
from app.settings import settings
from app.state import state

APP_ID = "0f3a9b2c-1111-4222-8333-444455556666"


def make_app(logo="https://example.invalid/firefox.png"):
    return InstalledApp(
        id=APP_ID,
        name="Firefox",
        logo=logo,
        url="x",
        source="Test Store",
        source_app_id="firefox",
        provider="docker",
        home_directories=True,
        users=["all"],
        groups=[],
        app_template="Default",
        provider_config={
            "image": "img:latest",
            "port": 3000,
            "nvidia_support": False,
            "dri3_support": True,
            "type": "browser",
            "url_support": True,
            "open_support": False,
            "extensions": [],
            "env": [],
            "docker_overrides": {},
        },
    )


@pytest.fixture
def http(tmp_path, monkeypatch):
    dist = tmp_path / "ui"
    (dist / "icons").mkdir(parents=True)
    (dist / "open.html").write_text(
        '<link rel="stylesheet" href="css/open.1.css"><script src="open.2.js"></script><a href="/ui/">x</a>'
        '<link rel="manifest" href="./manifest.json">'
    )
    (dist / "icons" / "icon128.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(settings, "ui_path", str(dist))
    state.installed_apps[APP_ID] = make_app()
    app = FastAPI()
    app.include_router(entry.router)
    return TestClient(app, follow_redirects=False)


def test_the_address_serves_the_page_with_its_assets_under_ui(http):
    page = http.get(f"/app/{APP_ID}/")
    assert page.status_code == 200
    assert 'href="/ui/css/open.1.css"' in page.text and 'src="/ui/open.2.js"' in page.text and 'href="/ui/"' in page.text
    assert 'href="./manifest.json"' in page.text
    assert page.headers["Content-Security-Policy"].startswith("frame-ancestors 'none'")
    assert http.get(f"/app/{APP_ID}?home=work").headers["location"] == f"/app/{APP_ID}/?home=work"
    assert http.get("/app/nope/").status_code == 404


def test_the_manifest_keeps_the_options_in_the_address():
    app = make_app()
    options = entry.clean_options({"home": "work", "room": "true", "wayland": "false", "gpu": "", "junk": "x", "lang": "de_DE.UTF-8"})
    assert options == {"home": "work", "room": "1", "lang": "de_DE.UTF-8", "wayland": "0"}
    manifest = entry.manifest_for(app, options, icon=f"/app/{APP_ID}/icon.png")
    assert manifest["start_url"] == manifest["id"] == f"/app/{APP_ID}/?home=work&room=1&lang=de_DE.UTF-8&wayland=0"
    assert manifest["scope"] == "/" and manifest["name"] == "Firefox"
    assert entry.address(APP_ID, {}) == f"/app/{APP_ID}/"


def test_the_icon_is_the_store_logo_or_the_uploaded_one(http, tmp_path):
    assert http.get(f"/app/{APP_ID}/icon.png").headers["location"] == "https://example.invalid/firefox.png"
    state.installed_apps[APP_ID] = make_app(logo=f"/api/app_icon/{APP_ID}")
    icons = tmp_path / "storage" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    (icons / f"{APP_ID}.png").write_bytes(b"\x89PNG-uploaded")
    icon = http.get(f"/app/{APP_ID}/icon.png")
    assert icon.status_code == 200 and icon.content == b"\x89PNG-uploaded"
    state.installed_apps[APP_ID] = make_app(logo="")
    assert http.get(f"/app/{APP_ID}/icon.png").content == b"\x89PNG"
