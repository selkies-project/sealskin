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
    for name in ("receive.html", "popup.html", "sw.js", "app.ABC123.js"):
        (dist / name).write_text(name)
    (dist / "index.html").write_text('<link rel="manifest" href="manifest.webmanifest"><script src="app.ABC123.js"></script>')
    monkeypatch.setattr(settings, "ui_path", str(dist))
    app = FastAPI()
    app.include_router(ui.router)
    ui.mount_ui(app)
    return TestClient(app)


def test_the_web_app_lives_at_the_root_and_its_old_address_leads_there(http):
    page = http.get("/")
    assert page.status_code == 200 and 'src="/ui/app.ABC123.js"' in page.text and 'href="/ui/manifest.webmanifest"' in page.text
    assert page.headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
    assert page.headers["Content-Security-Policy"].startswith("frame-ancestors 'none'; script-src 'self'")
    moved = http.get("/ui/?q=terms", follow_redirects=False)
    assert moved.status_code == 308 and moved.headers["location"] == "/?q=terms"
    assert http.get("/ui/index.html", follow_redirects=False).headers["location"] == "/"


def test_only_the_app_page_is_isolated(http):
    page = http.get("/ui/index.html", follow_redirects=False)
    assert page.status_code == 308
    page = http.get("/")
    assert page.headers["Cross-Origin-Opener-Policy"] == "same-origin-allow-popups"
    assert page.headers["Content-Security-Policy"].startswith("frame-ancestors 'none'; script-src 'self'")
    assert page.headers["Cache-Control"] == "no-cache"
    # Served pages stay frameable by the extension and the mobile app.
    framed = http.get("/ui/popup.html")
    assert "Cross-Origin-Opener-Policy" not in framed.headers
    assert "Content-Security-Policy" not in framed.headers
    # The pick bookmarklet's receive page keeps the opener that hands it the
    # file, so it carries the script CSP but not the opener policy.
    receive = http.get("/ui/receive.html")
    assert "Cross-Origin-Opener-Policy" not in receive.headers
    assert receive.headers["Content-Security-Policy"].startswith("frame-ancestors 'none'; script-src 'self'")
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
        {"action": "/", "accept": {"text/html": [".htm", ".html"], "application/pdf": [".pdf"]}}
    ]
    assert manifest["start_url"] == "/" and manifest["scope"] == "/"
    assert manifest["share_target"]["action"] == "share"
    assert manifest["share_target"]["params"]["files"][0]["name"] == "file"
    assert manifest["protocol_handlers"] == [{"protocol": "web+sealskin", "url": "/?url=%s"}]
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "1024x1024"}


def test_opensearch_template_names_the_address_the_browser_used(http):
    resp = http.get("/ui/opensearch.xml", headers={"Host": "sealskin.example:8443"})
    assert resp.headers["content-type"].startswith("application/opensearchdescription+xml")
    assert 'template="http://sealskin.example:8443/?q={searchTerms}"' in resp.text
    assert "http://sealskin.example:8443/ui/icons/icon128.png" in resp.text
