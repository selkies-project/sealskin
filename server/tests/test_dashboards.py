"""Dashboards: who gets which, what a link may be, and whose widgets are read."""

import httpx
import pytest
from pydantic import ValidationError

from app import branding, dashboards, netguard, widgets
from app.dashboards import Dashboard, LibraryWidget, Tile
from app.models import Branding
from app.settings import settings

ALICE = {"username": "alice@corp", "groups": ["ops"], "effective_settings": {}}
BOB = {"username": "bob", "groups": ["default"], "effective_settings": {"dashboard_feeds": False}}
FIXED = {"username": "carol", "groups": ["default"], "effective_settings": {"dashboard_customize": False}}


def link(**fields):
    return Tile(**{"id": "t1", "kind": "link", "name": "Wiki", "url": "https://wiki.corp/", **fields})


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "data:text/html,<script>1</script>", "vbscript:x", "file:///etc/passwd", "blob:https://x/1", "steam://run/1", "https://", "https://a b", "//wiki.corp/", "wiki.corp"],
)
def test_a_link_a_browser_would_run_or_no_scheme_lists_is_refused(url):
    with pytest.raises(ValidationError):
        link(url=url)


def test_listed_schemes_are_taken_and_the_never_list_overrides_the_setting(monkeypatch):
    assert link(url="ssh://{username}@bastion.corp").url == "ssh://{username}@bastion.corp"
    monkeypatch.setattr(settings, "dashboard_link_schemes", "steam, javascript, DATA:, rdp")
    assert dashboards.link_schemes() == ["http", "https", "steam", "rdp"]
    assert link(url="steam://run/1")
    for url in ("javascript:alert(1)", "data:text/html,x"):
        with pytest.raises(ValidationError):
            link(url=url)
    # A link opened in a session or in the page's own tab is a web address.
    for mode in ("same", "isolated"):
        with pytest.raises(ValidationError):
            link(url="rdp://host", open=mode)
    with pytest.raises(ValidationError):
        Tile(id="b", kind="bundle", name="Morning", urls=["https://a.corp/", "ssh://b"])


def test_a_user_gets_the_dashboards_that_name_them_with_their_name_filled_in():
    dashboards.save_library([
        LibraryWidget(id="ops-feed", name="Ops", type="text", config={"text": "hi"}, groups=["ops"]),
        LibraryWidget(id="news", name="News", type="text", config={"text": "all"}),
    ])
    dashboards.save_boards([
        Dashboard(id="ops", name="Ops", groups=["ops"], locked=True, tiles=[
            link(url="https://files.corp/~{username}/", x=2, y=1, w=3),
            Tile(id="w1", kind="widget", widget="ops-feed", x=5, y=1),
            Tile(id="w2", kind="widget", widget="gone"),
        ]),
        Dashboard(id="everyone", name="Everyone", tiles=[Tile(id="apps", kind="apps", x=4, w=2, y=3), Tile(id="w", kind="widget", widget="ops-feed")]),
    ])
    alice, bob = dashboards.view(ALICE), dashboards.view(BOB)
    assert [b["id"] for b in alice["boards"]] == ["ops", "everyone"] and [b["id"] for b in bob["boards"]] == ["everyone"]
    assert alice["boards"][0]["locked"] and "groups" not in alice["boards"][0]
    tiles = alice["boards"][0]["tiles"]
    assert [t["id"] for t in tiles] == ["t1", "w1"] and tiles[0]["url"] == "https://files.corp/~alice%40corp/"
    assert [(t["x"], t["y"], t["w"], t["h"]) for t in tiles] == [(2, 1, 3, 1), (5, 1, 1, 1)]
    # The catalog runs the width of the grid wherever it was put, and a widget for others is left out.
    assert [(t["kind"], t["x"], t["w"]) for t in bob["boards"][0]["tiles"]] == [("apps", 0, 12)]
    assert [w["id"] for w in alice["widgets"]] == ["ops-feed", "news"] and [w["id"] for w in bob["widgets"]] == ["news"]
    assert "config" not in alice["widgets"][0]


def test_without_dashboards_the_page_is_the_brands_links_and_the_applications():
    branding.save(Branding(links=[{"name": "Docs", "url": "https://docs.corp/", "open": "isolated"}]))
    board = dashboards.view(BOB)["boards"][0]
    assert board["id"] == "default" and [(t["kind"], t["y"]) for t in board["tiles"]] == [("link", 0), ("apps", 1)]
    assert board["tiles"][0] | {"id": ""} == Tile(id="x", kind="link", name="Docs", url="https://docs.corp/", open="isolated").model_dump() | {"id": "", "image": ""}


def test_two_tiles_of_one_id_and_two_catalogs_are_refused():
    with pytest.raises(ValidationError):
        Dashboard(id="d", name="D", tiles=[link(), link()])
    with pytest.raises(ValidationError):
        Dashboard(id="d", name="D", tiles=[Tile(id="a", kind="apps"), Tile(id="b", kind="apps")])
    # A tile is held to the grid's width.
    assert Tile(id="w", kind="heading", name="Tools", x=10, w=6).w == 2
    with pytest.raises(ValueError):
        dashboards.save_boards([Dashboard(id="d", name="D"), Dashboard(id="d", name="E")])


def test_a_record_edited_by_hand_keeps_its_valid_entries(tmp_path):
    import os

    import yaml

    os.makedirs(settings.dashboards_path, exist_ok=True)
    with open(os.path.join(settings.dashboards_path, "boards.yml"), "w", encoding="utf-8") as handle:
        yaml.safe_dump({"boards": [{"id": "ok", "name": "Fine"}, {"id": "bad", "name": "Bad", "tiles": [{"id": "t", "kind": "link", "name": "x", "url": "javascript:1"}]}, "junk"]}, handle)
    dashboards.load()
    assert [board.id for board in dashboards.boards()] == ["ok"]


async def test_a_widgets_data_is_read_only_for_whom_it_names_and_a_users_own_only_where_allowed(monkeypatch):
    dashboards.save_library([LibraryWidget(id="ops-note", name="Ops", type="text", config={"text": "secret plan"}, groups=["ops"])])
    assert (await dashboards.data_for(ALICE, {"widget": "ops-note"}))["data"]["text"] == "secret plan"
    assert (await dashboards.data_for(BOB, {"widget": "ops-note"})) == {"ok": False, "error": "No such widget."}
    # A user's own widget: never an administrator's type, never a feed where feeds are off, never anything where the page is fixed.
    assert (await dashboards.data_for(ALICE, {"type": "api", "config": {"url": "http://10.0.0.1/"}}))["ok"] is False
    assert "turned off" in (await dashboards.data_for(BOB, {"type": "feed", "config": {"urls": ["https://example.org/rss"]}}))["error"]
    assert (await dashboards.data_for(FIXED, {"type": "clock", "config": {}}))["ok"] is False
    assert (await dashboards.data_for(BOB, {"type": "clock", "config": {"timezone": "Europe/Berlin"}}))["data"]["timezone"] == "Europe/Berlin"

    async def resolve(host, port):
        return ["10.0.0.5"]

    monkeypatch.setattr(netguard, "_resolve", resolve)
    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(lambda request: httpx.Response(200, content=b"<rss><channel><item><title>x</title></item></channel></rss>")))
    widgets.reset()
    inside = await dashboards.data_for(ALICE, {"type": "feed", "config": {"urls": ["http://intranet.corp/rss"]}})
    assert inside["ok"] is False and "public internet" in inside["error"]


def test_what_a_user_may_change_follows_their_settings():
    assert dashboards.policy(ALICE) | {"schemes": []} == {"customize": True, "links": True, "feeds": True, "schemes": []}
    assert dashboards.policy(BOB)["feeds"] is False and dashboards.policy(BOB)["links"] is True
    assert dashboards.policy(FIXED) | {"schemes": []} == {"customize": False, "links": False, "feeds": False, "schemes": []}


async def test_an_administrator_sees_every_widget_of_the_library():
    dashboards.save_library([LibraryWidget(id="ops-note", name="Ops", type="text", config={"text": "plan"}, groups=["ops"])])
    admin = {"username": "root", "groups": ["default"], "is_admin": True, "effective_settings": {}}
    assert [w["id"] for w in dashboards.view(admin)["widgets"]] == ["ops-note"]
    assert (await dashboards.data_for(admin, {"widget": "ops-note"}))["ok"] is True


def test_a_dashboard_kept_as_sections_is_read_onto_the_grid():
    board = Dashboard.model_validate({"id": "old", "name": "Old", "sections": [
        {"id": "sessions", "kind": "sessions"},
        {"id": "s1", "name": "Work", "tiles": [{"id": "a", "kind": "heading", "name": "A", "w": 5}, {"id": "b", "kind": "heading", "name": "B", "w": 6, "h": 2}, {"id": "c", "kind": "heading", "name": "C", "w": 4}]},
        {"id": "apps", "kind": "apps"},
    ]})
    assert [(t.id, t.kind, t.x, t.y, t.w, t.h) for t in board.tiles] == [
        ("s1", "heading", 0, 0, 12, 1),
        ("a", "heading", 0, 1, 5, 1), ("b", "heading", 5, 1, 6, 2), ("c", "heading", 0, 3, 4, 1), ("apps", "apps", 0, 4, 12, 1),
    ]
    # The sessions are the page's to place: a tile kept for them is left out.
    assert [t.kind for t in Dashboard.model_validate({"id": "d", "name": "D", "tiles": [{"id": "sessions", "kind": "sessions"}, {"id": "apps", "kind": "apps"}]}).tiles] == ["apps"]


def test_icons_are_classes_pack_names_uploads_or_https_and_come_from_this_server():
    import base64
    import io

    from PIL import Image

    from app import logos

    for icon in ("fa-book", "fab fa-github", "di-plex", "sh-jellyfin", "si-github", "mdi-home-assistant", "https://corp/x.png", "icon:0123456789abcdef"):
        assert link(icon=icon).icon == icon
    for icon in ("javascript:alert(1)", "http://corp/x.png", "di-../../etc", "xx-plex", "icon:zz", "<img>"):
        with pytest.raises(ValidationError):
            link(icon=icon)
    plex = dashboards.icon_image("di-plex")
    assert plex.startswith(logos.ROUTE) and logos._known[plex[len(logos.ROUTE):]] == "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/plex.png"
    assert dashboards.pack_images(["di-plex", "fa-book", "https://evil/x", 5]) == {"di-plex": plex}
    buffer = io.BytesIO()
    Image.new("RGB", (800, 400), (200, 30, 30)).save(buffer, format="JPEG")
    name = dashboards.save_icon("data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode())
    served = dashboards.icon_response(name[5:])
    with Image.open(io.BytesIO(served.body)) as kept:
        assert (kept.format, kept.size) == ("PNG", (192, 96))
    assert dashboards.icon_image(name) == "/api/dashboard/icons/" + name[5:] and link(icon=name)
    dashboards.load()
    assert dashboards.icon_response(name[5:]).status_code == 200 and dashboards.icon_response("0" * 16).status_code == 404
    for data in ("not base64!", base64.b64encode(b"<svg onload=alert(1)>").decode(), base64.b64encode(b"MZ\x90\x00").decode()):
        with pytest.raises(ValueError):
            dashboards.save_icon(data)


async def test_a_sites_icon_is_read_through_the_guard_and_handed_back_as_a_png(monkeypatch):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGBA", (32, 32), (0, 120, 255, 255)).save(buffer, format="ICO")

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, content=b'<html><head><link rel="shortcut icon" href="/static/fav.ico"></head></html>')
        if request.url.path == "/static/fav.ico":
            return httpx.Response(200, content=buffer.getvalue())
        return httpx.Response(404)

    async def resolve(host, port):
        return ["10.0.0.5"] if host == "inside.corp" else ["93.184.216.34"]

    monkeypatch.setattr(netguard, "_resolve", resolve)
    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(handler))
    assert (await dashboards.site_icon("https://site.example/", public_only=True)).startswith("data:image/png;base64,iVBOR")
    assert (await dashboards.site_icon("http://inside.corp/", public_only=False)).startswith("data:image/png;base64,")
    with pytest.raises(ValueError):
        await dashboards.site_icon("http://inside.corp/", public_only=True)


async def test_a_links_status_is_told_only_to_whom_its_dashboard_names_and_only_when_asked_for(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(401 if "wiki" in str(request.url) else 503)

    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(handler))
    widgets.reset()
    dashboards.save_boards([Dashboard(id="ops", name="Ops", groups=["ops"], isolated_app="firefox", tiles=[
        link(id="wiki", url="http://wiki.lan/~{username}", open="isolated", status=True, vpn="office", vpn_endpoint="e1"),
        link(id="down", url="http://down.lan/", status=True, vpn="office", x=1),
        link(id="quiet", url="http://quiet.lan/", x=2),
        link(id="ssh", url="ssh://bastion.lan", status=True, x=3),
    ])])
    board = dashboards.view(ALICE)["boards"][0]
    assert board["isolated_app"] == "firefox"
    assert [(t["id"], t["status"], t["vpn"], t["vpn_endpoint"]) for t in board["tiles"]] == [("wiki", True, "office", "e1"), ("down", True, "", ""), ("quiet", False, "", ""), ("ssh", False, "", "")]
    # An answer that asks for a sign-in is still an answer; a server error is not.
    assert (await dashboards.data_for(ALICE, {"link": "wiki", "board": "ops"}))["data"]["up"] is True and seen == ["http://wiki.lan/~alice%40corp"]
    assert (await dashboards.data_for(ALICE, {"link": "down", "board": "ops"}))["data"]["up"] is False
    for user, item in ((BOB, {"link": "wiki", "board": "ops"}), (ALICE, {"link": "quiet", "board": "ops"}), (ALICE, {"link": "wiki", "board": "other"})):
        assert await dashboards.data_for(user, item) == {"ok": False, "error": "No such link."}
    assert len(seen) == 2

