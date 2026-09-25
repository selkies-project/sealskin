"""The web app as served: isolation headers, the manifest, and the OpenSearch description."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config_store
from app.models import InstalledAppRecord
from app.routers import ui
from app.settings import settings

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def http(tmp_path, monkeypatch):
    dist = tmp_path / "ui"
    dist.mkdir()
    for name in ("index.html", "popup.html", "sw.js", "app.ABC123.js"):
        (dist / name).write_text(name)
    monkeypatch.setattr(settings, "ui_path", str(dist))
    app = FastAPI()
    app.include_router(ui.router)
    ui.mount_ui(app)
    return TestClient(app)


def test_only_the_app_page_is_isolated(http):
    page = http.get("/ui/")
    assert page.text == "index.html"
    assert page.headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
    assert page.headers["Content-Security-Policy"].startswith("frame-ancestors 'none'; script-src 'self'")
    assert page.headers["Cache-Control"] == "no-cache"
    # Served pages stay frameable by the extension and the mobile app.
    framed = http.get("/ui/popup.html")
    assert "Cross-Origin-Opener-Policy" not in framed.headers
    assert "Content-Security-Policy" not in framed.headers
    assert http.get("/ui/sw.js").headers["Cache-Control"] == "no-cache"
    assert "immutable" in http.get("/ui/app.ABC123.js").headers["Cache-Control"]


def test_manifest_opens_what_installed_apps_open(http, store_with_firefox):
    assert "file_handlers" not in http.get("/ui/manifest.webmanifest").json()
    config_store.set_record(InstalledAppRecord(id="app-1", source="Test Store", source_app_id="firefox"))
    config_store.set_record(
        InstalledAppRecord(
            id="app-2",
            source="Test Store",
            source_app_id="firefox",
            overrides={"provider_config": {"extensions": [".PDF", "unknownext", ""]}},
        )
    )
    resp = http.get("/ui/manifest.webmanifest")
    assert resp.headers["content-type"] == "application/manifest+json"
    manifest = resp.json()
    assert manifest["file_handlers"] == [
        {"action": "./", "accept": {"text/html": [".htm", ".html"], "application/pdf": [".pdf"]}}
    ]
    assert manifest["share_target"]["action"] == "share"
    assert manifest["share_target"]["params"]["files"][0]["name"] == "file"
    assert manifest["protocol_handlers"] == [{"protocol": "web+sealskin", "url": "./?url=%s"}]
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "1024x1024"}


def test_opensearch_template_names_the_address_the_browser_used(http):
    resp = http.get("/ui/opensearch.xml", headers={"Host": "sealskin.example:8443"})
    assert resp.headers["content-type"].startswith("application/opensearchdescription+xml")
    assert 'template="http://sealskin.example:8443/ui/?q={searchTerms}"' in resp.text
