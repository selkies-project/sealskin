"""The brand: its record, its pictures, and the pages wearing it."""

import base64
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import branding
from app.models import Branding
from app.routers import branding as branding_routes
from app.routers import ui
from app.settings import settings
from app.state import state

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

PAGE = (
    '<!DOCTYPE html><html><head><title>SealSkin</title><link rel="icon" href="icons/icon128.png"></head>'
    '<body><a class="project-link"><img class="rail-logo" src="icons/icon128.png" alt="SealSkin"></a>'
    '<span class="wordmark">SealSkin</span><h1>SealSkin</h1><p>SealSkin</p></body></html>'
)


def picture(width=64, height=32, fmt="PNG"):
    """A small picture of the given format, base64 encoded."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format=fmt)
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def http(tmp_path, monkeypatch):
    dist = tmp_path / "ui"
    dist.mkdir()
    (dist / "index.html").write_text(PAGE)
    (dist / "home.html").write_text(PAGE)
    monkeypatch.setattr(settings, "ui_path", str(dist))
    app = FastAPI()
    app.include_router(ui.router)
    app.include_router(branding_routes.router)
    app.include_router(branding_routes.user_router)
    ui.mount_ui(app)
    return TestClient(app)


def test_the_defaults_leave_the_pages_as_built(http):
    page = http.get("/").text
    assert "<title>SealSkin</title>" in page and 'href="/ui/icons/icon128.png"' in page
    assert '<meta name="application-name" content="SealSkin">' in page
    assert "<style>" not in page
    assert http.get("/ui/manifest.webmanifest").json()["name"] == "SealSkin"
    assert branding.view().is_default


def test_a_saved_brand_dresses_every_served_page(http):
    branding.save(Branding(name="Acme Desktops", logo_link="", accent="#336699"))
    branding.save_image("logo", picture())
    for path in ("/", "/ui/home.html"):
        page = http.get(path).text
        assert "<title>Acme Desktops</title>" in page
        assert '<span class="wordmark">Acme Desktops</span><h1>Acme Desktops</h1><p>SealSkin</p>' in page
        assert '<meta name="application-name" content="Acme Desktops">' in page
        assert "&quot;logoLink&quot;:&quot;&quot;" in page and "&quot;projectLink&quot;:false" in page
        assert "--accent-primary:#336699" in page and "--accent-contrast:#ffffff" in page
        assert 'href="/api/branding/logo?v=' in page and 'src="/api/branding/logo?v=' in page
        assert 'alt="Acme Desktops"' in page
    manifest = http.get("/ui/manifest.webmanifest").json()
    assert manifest["name"] == "Acme Desktops"
    assert manifest["icons"] == [{"src": branding.image("logo").url, "sizes": "64x32", "type": "image/png"}]
    assert "<ShortName>Acme Desktops</ShortName>" in http.get("/ui/opensearch.xml").text
    served = http.get(branding.image("logo").url)
    assert served.status_code == 200 and served.headers["content-type"] == "image/png" and "immutable" in served.headers["cache-control"]
    assert http.get("/api/branding/wallpaper").status_code == 404


def test_pictures_are_checked_and_replaced_by_kind(http):
    branding.save_image("wallpaper", picture(300, 200, "JPEG"))
    assert branding.image("wallpaper").type == "image/jpeg"
    first = branding.image("wallpaper").url
    branding.save_image("wallpaper", picture(300, 200, "WEBP"))
    assert branding.image("wallpaper").type == "image/webp" and branding.image("wallpaper").url != first
    # The earlier file of another format is gone, so only one wallpaper is kept.
    assert sorted(name.split(".")[0] for name in __import__("os").listdir(settings.branding_path)) == ["wallpaper"]
    with pytest.raises(Exception, match="not a PNG"):
        branding.save_image("logo", base64.b64encode(b"not a picture").decode())
    with pytest.raises(Exception, match="at most"):
        branding.save_image("logo", base64.b64encode(b"\x89PNG" + b"0" * 1_100_000).decode())
    with pytest.raises(Exception, match="logo or a wallpaper"):
        branding.save_image("banner", picture())
    branding.reset()
    assert branding.view().is_default and branding.image("wallpaper") is None


def test_a_broken_record_leaves_the_defaults(tmp_path):
    import os

    os.makedirs(settings.branding_path, exist_ok=True)
    with open(os.path.join(settings.branding_path, "branding.yml"), "w") as handle:
        handle.write("name: ''\naccent: red\n")
    branding.load()
    assert state.branding == Branding()


def test_links_and_the_logo_link_are_checked():
    with pytest.raises(ValueError):
        Branding(logo_link="javascript:alert(1)")
    with pytest.raises(ValueError):
        Branding(links=[{"name": "Wiki", "url": "ftp://wiki"}])
    with pytest.raises(ValueError):
        Branding(links=[{"name": "Wiki", "url": "https://wiki", "icon": "<img>"}])
    brand = Branding(links=[{"name": "Wiki", "url": "https://wiki", "icon": "fa-book", "open": "isolated"}])
    assert brand.links[0].open == "isolated"
