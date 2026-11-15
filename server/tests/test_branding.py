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


def test_a_variant_is_worn_at_its_host_names_and_by_its_groups_with_its_own_pictures(http):
    from app.models import BrandVariant

    branding.save(Branding(name="Acme", accent="#336699", variants=[
        BrandVariant(id="render", label="Render farm", hosts=["Render.Example.org", "*.farm.example.org"], name="Render Farm", accent="#aa3300", wallpaper_forced=True),
        BrandVariant(id="ops", label="Operations", groups=["ops"], name="Ops Portal"),
    ]))
    branding.save_image("logo", picture())
    branding.save_image("logo", picture(40, 40), "render")
    branding.save_image("wallpaper", picture(300, 200, "JPEG"), "ops")
    assert [branding.choose(host) for host in ("render.example.org:8443", "a.farm.example.org", "farm.example.org", "portal.example.org", "[::1]:8443", "")] == ["render", "render", "", "", "", ""]
    assert branding.choose("portal.example.org", ["staff", "ops"]) == "ops" and branding.choose("render.example.org", ["ops"]) == "render"
    # The pages wear the brand of the host name they were asked at.
    plain, farm = http.get("/", headers={"host": "portal.example.org"}).text, http.get("/ui/home.html", headers={"host": "render.example.org"}).text
    assert "<title>Acme</title>" in plain and "--accent-primary:#336699" in plain and "&b=" not in plain
    assert "<title>Render Farm</title>" in farm and "--accent-primary:#aa3300" in farm and "&amp;b=render" in farm
    assert http.get("/ui/manifest.webmanifest", headers={"host": "render.example.org"}).json()["name"] == "Render Farm"
    # A variant's own picture is served under its id; where it has none, the brand's stands in.
    render, ops = branding.view("render"), branding.view("ops")
    assert render.logo.width == 40 and render.wallpaper is None and render.id == "render" and render.hosts == ["render.example.org", "*.farm.example.org"]
    assert ops.logo.url == branding.view().logo.url and ops.wallpaper.url.endswith("&b=ops") and branding.view().wallpaper is None
    assert http.get(render.logo.url).content != http.get(ops.logo.url).content and http.get("/api/branding/logo?b=../../x").status_code == 200
    assert [item.name for item in branding.views()] == ["Acme", "Render Farm", "Ops Portal"]
    # Taking a variant out of the record takes its pictures with it.
    branding.save(Branding(name="Acme", variants=[BrandVariant(id="ops", label="Operations", groups=["ops"])]))
    assert branding.choose("render.example.org") == "" and "logo.render" not in branding._images and "wallpaper.ops" in branding._images
    for bad in ({"hosts": ["https://x"]}, {"hosts": ["x:8443"]}, {"id": "a.b"}):
        with pytest.raises(ValueError):
            BrandVariant(**{"id": "v", "label": "V", **bad})
    with pytest.raises(ValueError):
        Branding(variants=[BrandVariant(id="v", label="A"), BrandVariant(id="v", label="B")])
