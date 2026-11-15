"""Dashboards: the home pages an administrator lays out for users, and the widget library they draw on.

A dashboard is a named page with one grid, `COLUMNS` cells wide, and every
tile sits where it was put: at a column and a row, so many cells wide and
high. A tile is an application, a link, a bundle of links opened together,
a widget, or a heading; one more is the page's own, the catalog of every
application the user may launch, which runs the width of the grid on a row
of its own, or is left out. The running sessions are not a dashboard's to
place: the page shows them above it. A `kiosk` dashboard is all its users
see of the page, which is a matter of what the page offers: what a user may
launch is still what the applications' own lists give them. Each dashboard
names the users and groups it is for, as an application does, and a user
gets every one that names them, in the administrator's order, as pages to
switch between; where none does, they get `default_board`, which is the
page as it is without dashboards, the brand's links included.

The dashboards are the shared object `dashboards/boards.yml`, the widget
library `dashboards/widgets.yml`, and the pictures an administrator uploads
as icons, `dashboards/icons/<hash>.<ext>`, read by every node; the two
records can be written by hand. What a user changes on their page is kept
by their browser, laid over the dashboard it came from; a `locked`
dashboard is shown as the administrator made it. Neither the lock nor the
`dashboard_*` user settings are a boundary by themselves: a page a user
rearranges is seen by that user alone. What the server enforces is what
reaches it: a widget's data is read only for a user the widget names, and a
user's own widget only where the settings allow one (`data_for`).

A link is `http`, `https`, or a scheme the `dashboard_link_schemes` setting
lists; `SCHEMES_NEVER` are refused whatever the setting says, since a
browser runs or reads them in the page that holds the link. `{username}`
in a link is replaced by the signed-in user's name, percent-encoded whole.

A link opened in a session (`isolated`) is how a user anywhere reaches an
address only the server's network can: the session's browser is inside it.
Such a link may name the application it opens in, or leave that to the
dashboard's `isolated_app`, and the VPN profile its session leaves through,
which the launch still checks against the user. An administrator's link may
ask for a `status` dot: this node then tells the page whether the address
answers (`data_for`), for the users the dashboard names alone, since the
answer says what is up inside the network.

A tile's icon is Font Awesome classes, a name from one of `ICON_PACKS`
(`di-plex`, `sh-jellyfin`, `si-github`, `mdi-home`), an uploaded picture
(`icon:<hash>`), or an `https` address. No page loads a picture from
elsewhere: a pack's and an address's are fetched by this node
(`app.logos`), and `site_icon` reads a site's own icon for a tile.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import logging
import os
import re
from typing import Any
from urllib.parse import quote, urljoin

from fastapi import Response
from lxml import etree
from lxml import html as lxml_html
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import logos, netguard, persistence, widgets
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

BOARDS_NAME = "boards.yml"
WIDGETS_NAME = "widgets.yml"
#: Schemes no link may have: the browser runs or reads them inside the page.
SCHEMES_NEVER = frozenset(
    {
        "javascript",
        "data",
        "vbscript",
        "blob",
        "file",
        "about",
        "view-source",
        "filesystem",
        "chrome",
        "chrome-extension",
        "moz-extension",
        "resource",
        "jar",
        "ws",
        "wss",
    }
)
WEB_SCHEMES = ("http", "https")
_ID = r"^[A-Za-z0-9_-]{1,48}$"
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_ICON = re.compile(r"(fa[sbrl]? )?fa-[a-z0-9-]+")
#: Icon packs by prefix: where this node fetches the icon a name stands for.
ICON_PACKS = {
    "di": "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/{name}.png",
    "sh": "https://cdn.jsdelivr.net/gh/selfhst/icons/png/{name}.png",
    "si": "https://cdn.simpleicons.org/{name}/white",
    "mdi": "https://cdn.jsdelivr.net/npm/@mdi/svg/svg/{name}.svg",
}
_PACK = re.compile(r"(" + "|".join(ICON_PACKS) + r")-([a-z0-9][a-z0-9-]{0,60})")
_UPLOADED = re.compile(r"icon:([0-9a-f]{16})")
ICONS_DIR = "icons"
ICON_ROUTE = "/api/dashboard/icons/"
#: Bytes an uploaded icon may be at most, and the pixels of its longer edge once kept.
ICON_BYTES = 1_000_000
ICON_EDGE = 192
ICON_FORMATS = ("PNG", "JPEG", "WEBP", "GIF", "ICO", "BMP")
COLUMNS = 12
MAX_ROWS = 8
MAX_TILES = 300
USERNAME = "{username}"
MAX_BOARDS = 50
MAX_WIDGETS = 200

#: The dashboards and the library as last read, in the administrator's order, and the uploaded icons by hash.
_boards: list[Dashboard] = []
_library: dict[str, LibraryWidget] = {}
_icons: dict[str, bytes] = {}


def link_schemes() -> list[str]:
    """Return the schemes a link may have: the web's, and those `dashboard_link_schemes` lists."""
    listed = [name.strip().lower().rstrip(":") for name in str(settings.dashboard_link_schemes or "").split(",")]
    extra = [name for name in listed if re.fullmatch(r"[a-z][a-z0-9+.-]*", name) and name not in SCHEMES_NEVER]
    return list(dict.fromkeys([*WEB_SCHEMES, *extra]))


def check_link(url: str, web_only: bool = False) -> str:
    """Return a link trimmed, or raise `ValueError` for one a tile may not carry.

    Args:
        url: The address, which may hold `{username}` anywhere after its scheme.
        web_only: Take `http` and `https` alone, as a link opened in a session or in the page's own tab must be.
    """
    url = url.strip()
    if not url or len(url) > 2000 or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("A link is an address without spaces, at most 2000 characters.")
    match = _SCHEME.match(url)
    scheme = match.group(1).lower() if match else ""
    allowed = WEB_SCHEMES if web_only else link_schemes()
    if scheme not in allowed:
        raise ValueError(f"A link starts with one of: {', '.join(f'{name}:' for name in allowed)}.")
    if scheme in WEB_SCHEMES and not re.fullmatch(r"https?://[^/?#]+.*", url, re.IGNORECASE):
        raise ValueError("A web link names a host.")
    return url


def _icon(value: str) -> str:
    """Return an icon as Font Awesome classes, a pack's name, an uploaded picture, or an `https` picture, or raise `ValueError`."""
    value = " ".join(value.split())
    if value and not (_ICON.fullmatch(value) or _PACK.fullmatch(value) or _UPLOADED.fullmatch(value) or re.fullmatch(r"https://\S+", value)):
        raise ValueError("An icon is a Font Awesome class, a name such as di-plex or si-github, an uploaded picture, or an https address.")
    return value


def icon_image(icon: str) -> str:
    """Return where a page loads an icon's picture from, on this server; empty for Font Awesome classes."""
    pack = _PACK.fullmatch(icon)
    if pack:
        return logos.local(ICON_PACKS[pack.group(1)].format(name=pack.group(2)))
    uploaded = _UPLOADED.fullmatch(icon)
    if uploaded:
        return ICON_ROUTE + uploaded.group(1)
    return logos.local(icon) if icon.startswith("https://") else ""


class Tile(BaseModel):
    """One tile of a dashboard's grid.

    Attributes:
        id: Its name within the dashboard, which the user's own layout refers to.
        kind: `app`, `link`, `bundle`, `widget`, `heading`, or the page's own `apps`.
        x: The column it starts at, from 0.
        y: The row it starts at, from 0.
        w: Cells it is wide; `apps` runs the whole width.
        h: Cells it is high; `apps` takes one row and grows with what it holds. A row that holds headings alone is only as high as they are.
        name: The label; an application's own name and a widget's stand in for an empty one.
        app: The application of an `app` tile, or the one an `isolated` link opens in; empty leaves the user to pick.
        url: Where a `link` leads.
        urls: The addresses a `bundle` opens together, each in a tab.
        open: How a link opens: `tab` in a new tab, `same` in the page's own, `isolated` in a session.
        icon: See `_icon`.
        description: A line shown on a tile wider than one cell.
        widget: The library widget of a `widget` tile.
        scale: The size of a heading's text: `s`, `m`, or `l`.
        status: The tile of a web link shows whether its address answers this node.
        vpn: Id of the VPN profile an `isolated` link's session leaves through; empty for none.
        vpn_endpoint: Id of that profile's endpoint; empty for its first.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=_ID)
    kind: str = Field(pattern=r"^(app|link|bundle|widget|heading|apps)$")
    x: int = Field(default=0, ge=0, le=COLUMNS - 1)
    y: int = Field(default=0, ge=0, le=2000)
    w: int = Field(default=1, ge=1, le=COLUMNS)
    h: int = Field(default=1, ge=1, le=MAX_ROWS)
    name: str = Field(default="", max_length=80)
    app: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=2000)
    urls: list[str] = Field(default_factory=list, max_length=20)
    open: str = Field(default="tab", pattern=r"^(tab|same|isolated)$")
    icon: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=200)
    widget: str = Field(default="", max_length=48)
    scale: str = Field(default="m", pattern=r"^(s|m|l)$")
    status: bool = False
    vpn: str = Field(default="", pattern=r"^[A-Za-z0-9_-]{0,64}$")
    vpn_endpoint: str = Field(default="", pattern=r"^[A-Za-z0-9_-]{0,64}$")

    _icon_shape = field_validator("icon")(lambda cls, value: _icon(value))

    @model_validator(mode="after")
    def _whole(self) -> Tile:
        """Hold each kind to what it needs, and the tile to the grid."""
        if self.kind == "apps":
            self.x, self.w, self.h = 0, COLUMNS, 1
        self.w = min(self.w, COLUMNS - self.x)
        if self.kind == "app" and not self.app:
            raise ValueError("An application tile names its application.")
        if self.kind == "link":
            self.url = check_link(self.url, web_only=self.open != "tab")
            self.status = self.status and self.url.lower().startswith(("http://", "https://"))
        else:
            self.status = False
        if self.kind != "link" or self.open != "isolated":
            self.vpn = self.vpn_endpoint = ""
        if self.kind == "bundle":
            self.urls = [check_link(url, web_only=True) for url in self.urls]
            if not self.urls:
                raise ValueError("A bundle holds at least one link.")
        if self.kind in ("link", "bundle", "heading") and not self.name.strip():
            raise ValueError("The tile needs a name.")
        if self.kind == "widget" and not re.fullmatch(_ID, self.widget):
            raise ValueError("A widget tile names a widget of the library.")
        return self


class Look(BaseModel):
    """How a dashboard's tiles look until a user picks otherwise.

    Attributes:
        style: `solid` cards, `glass` over the background, or `flat` with no card at all.
        labels: Whether application and link tiles show their names.
        density: `compact`, `normal`, or `roomy` rows.
        width: `normal` for a centered column, `wide` for the whole window.
    """

    model_config = ConfigDict(extra="ignore")

    style: str = Field(default="solid", pattern=r"^(solid|glass|flat)$")
    labels: bool = True
    density: str = Field(default="normal", pattern=r"^(compact|normal|roomy)$")
    width: str = Field(default="normal", pattern=r"^(normal|wide)$")


def _from_sections(sections: Any) -> list[dict[str, Any]]:
    """Return the tiles of a dashboard kept as sections of ordered tiles, each given a place in reading order."""
    tiles: list[dict[str, Any]] = []
    row = 0
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict):
            continue
        if section.get("kind") == "sessions":
            continue
        if section.get("kind") == "apps":
            tiles.append({"id": section.get("id"), "kind": "apps", "y": row})
            row += 1
            continue
        if section.get("name"):
            tiles.append({"id": section.get("id"), "kind": "heading", "name": section["name"], "x": 0, "y": row, "w": COLUMNS})
            row += 1
        column = height = 0
        for tile in section.get("tiles") or []:
            if not isinstance(tile, dict):
                continue
            wide, high = min(COLUMNS, int(tile.get("w") or 1)), min(MAX_ROWS, int(tile.get("h") or 1))
            if column + wide > COLUMNS:
                row, column, height = row + height, 0, 0
            tiles.append({**tile, "x": column, "y": row, "w": wide, "h": high})
            column, height = column + wide, max(height, high)
        row += height
    return tiles


class Dashboard(BaseModel):
    """A home page an administrator laid out.

    Attributes:
        id: Its name, which a user's own layout of it is kept under.
        name: What the page's tab says.
        icon: Font Awesome classes for the tab.
        groups: Groups the dashboard is for; `all` is everyone.
        users: Users it is for besides.
        locked: Users see it as it is and cannot rearrange it.
        look: How its tiles look until a user picks otherwise.
        kiosk: The page shows this dashboard and nothing else: no search box, no catalog beyond the one placed on it, no pages of the user's own, no edit mode.
        isolated_app: The application a link opened in a session uses when its tile names none; empty leaves the user to pick.
        tiles: Its tiles, each at its place on the grid.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=60)
    icon: str = Field(default="", max_length=60)
    groups: list[str] = Field(default_factory=lambda: ["all"], max_length=200)
    users: list[str] = Field(default_factory=list, max_length=500)
    locked: bool = False
    look: Look = Field(default_factory=Look)
    kiosk: bool = False
    isolated_app: str = Field(default="", max_length=200)
    tiles: list[Tile] = Field(default_factory=list, max_length=MAX_TILES)

    @model_validator(mode="before")
    @classmethod
    def _sections(cls, value: Any) -> Any:
        """Read a dashboard kept as sections of ordered tiles, and leave out a tile for the sessions, which the page places itself."""
        if isinstance(value, dict) and "tiles" not in value and "sections" in value:
            value = {**value, "tiles": _from_sections(value["sections"])}
        if isinstance(value, dict) and isinstance(value.get("tiles"), list):
            value = {**value, "tiles": [tile for tile in value["tiles"] if not (isinstance(tile, dict) and tile.get("kind") == "sessions")]}
        return value

    @field_validator("icon")
    @classmethod
    def _classes(cls, value: str) -> str:
        """Keep the tab's icon to Font Awesome classes."""
        value = " ".join(value.split())
        if value and not _ICON.fullmatch(value):
            raise ValueError("A dashboard's icon is a Font Awesome class.")
        return value

    @model_validator(mode="after")
    def _distinct(self) -> Dashboard:
        """Refuse two tiles of one name, and a second `apps` tile."""
        names = [tile.id for tile in self.tiles]
        if len(names) != len(set(names)):
            raise ValueError("Every tile of a dashboard has its own id.")
        if sum(tile.kind == "apps" for tile in self.tiles) > 1:
            raise ValueError("A dashboard has one apps tile at most.")
        return self


class LibraryWidget(BaseModel):
    """A widget an administrator defined, which dashboards and users place as a tile.

    Attributes:
        id: Its name, which tiles refer to.
        name: The title on its tile.
        type: One of `widgets.TYPES`.
        config: What the type is configured with (see `app.widgets`).
        groups: Groups that may see it; `all` is everyone.
        users: Users that may besides.
        refresh: Seconds its data is kept; 0 for the type's own.
        w: Cells its tile is wide when placed.
        h: Cells its tile is high when placed.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=_ID)
    name: str = Field(min_length=1, max_length=80)
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    groups: list[str] = Field(default_factory=lambda: ["all"], max_length=200)
    users: list[str] = Field(default_factory=list, max_length=500)
    refresh: int = Field(default=0, ge=0, le=86400)
    w: int = Field(default=3, ge=1, le=COLUMNS)
    h: int = Field(default=2, ge=1, le=MAX_ROWS)

    @model_validator(mode="after")
    def _typed(self) -> LibraryWidget:
        """Hold the configuration to its type."""
        self.config = widgets.validate(self.type, self.config)
        return self


def _dir() -> str:
    return settings.dashboards_path


def _read(name: str, key: str, model: type[BaseModel], limit: int) -> list[Any]:
    """Read one of the two records, leaving out what does not validate."""
    path = os.path.join(_dir(), name)
    try:
        record = persistence.read_yaml(path, None)
    except Exception as exc:  # noqa: BLE001 - a broken file leaves nothing defined
        logger.warning("Could not read %s: %s", path, exc)
        return []
    entries = record.get(key) if isinstance(record, dict) else None
    found: list[Any] = []
    seen: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        try:
            item = model.model_validate(entry)
        except ValidationError as exc:
            logger.warning("Ignoring an entry of %s: %s", path, exc.errors()[0]["msg"])
            continue
        if item.id not in seen and len(found) < limit:
            seen.add(item.id)
            found.append(item)
    return found


def load() -> None:
    """Read the dashboards, the widget library, and the uploaded icons, and note the pictures the tiles name."""
    global _boards, _library
    _boards = _read(BOARDS_NAME, "boards", Dashboard, MAX_BOARDS)
    _library = {widget.id: widget for widget in _read(WIDGETS_NAME, "widgets", LibraryWidget, MAX_WIDGETS)}
    widgets.reset()
    for board in _boards:
        for tile in board.tiles:
            icon_image(tile.icon)
    _icons.clear()
    folder = os.path.join(_dir(), ICONS_DIR)
    try:
        for name in persistence.list_names(folder):
            stem = name.split(".")[0]
            data = persistence.read_bytes(os.path.join(folder, name)) if re.fullmatch(r"[0-9a-f]{16}", stem) else None
            if data:
                _icons[stem] = data
    except Exception as exc:  # noqa: BLE001 - the store may be away; the tiles keep their fallback icon
        logger.warning("Could not read the dashboards' icons: %s", exc)


def _png(data: bytes) -> bytes:
    """Return a picture as a PNG no larger than `ICON_EDGE`, or raise `ValueError` for anything but a picture."""
    try:
        with Image.open(io.BytesIO(data)) as picture:
            if picture.format not in ICON_FORMATS or max(picture.size) > 4096:
                raise ValueError("An icon is a PNG, JPEG, WebP, GIF, or ICO picture, at most 4096 pixels wide.")
            picture = picture.convert("RGBA")
            picture.thumbnail((ICON_EDGE, ICON_EDGE))
            out = io.BytesIO()
            picture.save(out, format="PNG")
            return out.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("An icon is a PNG, JPEG, WebP, GIF, or ICO picture.") from exc


def save_icon(encoded: str) -> str:
    """Keep an uploaded picture as an icon, and return the `icon:<hash>` a tile names it by.

    Raises:
        ValueError: For data that is not base64, not a picture, or larger than `ICON_BYTES`.
    """
    try:
        data = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("The icon is not base64 encoded.") from exc
    if not data or len(data) > ICON_BYTES:
        raise ValueError("An icon is at most 1 MB.")
    picture = _png(data)
    name = hashlib.sha256(picture).hexdigest()[:16]
    if name not in _icons:
        persistence.write_bytes(os.path.join(_dir(), ICONS_DIR, f"{name}.png"), picture)
        _icons[name] = picture
    return f"icon:{name}"


def icon_response(name: str) -> Response:
    """Answer with an uploaded icon, cached for a year under its hash; 404 for none by that name."""
    data = _icons.get(name)
    if data is None:
        return Response(status_code=404)
    return Response(
        data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


async def site_icon(url: str, public_only: bool) -> str:
    """Return a site's own icon as a PNG data URL: the one its page names, or its `/favicon.ico`.

    Args:
        url: The site's `http` or `https` address.
        public_only: The address is a user's: fetch nothing that is not of the public internet.

    Raises:
        ValueError: Where the site has no icon this server can read.
    """
    candidates: list[str] = []
    try:
        page = await netguard.fetch(url, public_only=public_only, max_bytes=1_000_000, timeout=8.0)
        if page.status < 400 and page.content.strip():
            for link in lxml_html.fromstring(page.content).iterfind(".//link[@href]"):
                if "icon" in (link.get("rel") or "").lower():
                    candidates.append(urljoin(url, link.get("href")))
    except (netguard.Refused, ValueError, etree.LxmlError):
        pass
    candidates.append(urljoin(url, "/favicon.ico"))
    for candidate in candidates[:6]:
        if not candidate.startswith(("http://", "https://")):
            continue
        try:
            fetched = await netguard.fetch(candidate, public_only=public_only, max_bytes=ICON_BYTES, timeout=8.0)
            if fetched.status < 400:
                return "data:image/png;base64," + base64.b64encode(_png(fetched.content)).decode()
        except (netguard.Refused, ValueError):
            continue
    raise ValueError("The site has no icon this server can read.")


def _distinct(items: list[Any], limit: int, what: str) -> None:
    if len(items) > limit:
        raise ValueError(f"At most {limit} {what}.")
    if len({item.id for item in items}) != len(items):
        raise ValueError(f"Every one of the {what} has its own id.")


def save_boards(boards: list[Dashboard]) -> None:
    """Write the dashboards, in the order given, and take them up.

    Raises:
        ValueError: For more than `MAX_BOARDS` or two of one id.
    """
    _distinct(boards, MAX_BOARDS, "dashboards")
    persistence.write_yaml_sync(os.path.join(_dir(), BOARDS_NAME), {"boards": [board.model_dump() for board in boards]})
    load()


def save_library(library: list[LibraryWidget]) -> None:
    """Write the widget library and take it up.

    Raises:
        ValueError: For more than `MAX_WIDGETS` or two of one id.
    """
    _distinct(library, MAX_WIDGETS, "widgets")
    persistence.write_yaml_sync(os.path.join(_dir(), WIDGETS_NAME), {"widgets": [widget.model_dump() for widget in library]})
    load()


def boards() -> list[Dashboard]:
    """Return every dashboard, in the administrator's order."""
    return list(_boards)


def library() -> list[LibraryWidget]:
    """Return every widget of the library."""
    return list(_library.values())


def _named(users: list[str], groups: list[str], user: dict[str, Any]) -> bool:
    """Tell whether an access list names a user: by `all`, by name, or by one of their groups."""
    held = set(user.get("groups") or [])
    return "all" in users or user["username"] in users or "all" in groups or bool(held & set(groups))


def _may_see(widget: LibraryWidget, user: dict[str, Any]) -> bool:
    """Tell whether a user may see a widget: one it names, or an administrator, who lays every one out."""
    return bool(user.get("is_admin")) or _named(widget.users, widget.groups, user)


def default_board() -> Dashboard:
    """Return the page of a user no dashboard names: the brand's links, and the applications."""
    tiles = []
    row = 0
    for index, link in enumerate(state.branding.links):
        tiles.append(
            Tile(id=f"brand-link-{index}", kind="link", name=link.name, url=link.url, icon=link.icon, open=link.open, x=index % COLUMNS, y=row + index // COLUMNS)
        )
    row += -(-len(state.branding.links) // COLUMNS)
    tiles.append(Tile(id="apps", kind="apps", y=row))
    return Dashboard(id="default", name="Home", tiles=tiles)


def policy(user: dict[str, Any]) -> dict[str, Any]:
    """Return what a user may do with their page, and the schemes a link of theirs may have."""
    effective = user.get("effective_settings") or {}
    customize = bool(effective.get("dashboard_customize", True))
    return {
        "customize": customize,
        "links": customize and bool(effective.get("dashboard_links", True)),
        "feeds": customize and bool(effective.get("dashboard_feeds", True)),
        "schemes": link_schemes(),
    }


def _tile_view(tile: Tile, user: dict[str, Any]) -> dict[str, Any] | None:
    """Return a tile as a user's page reads it, or `None` for a widget the user may not see."""
    shown = tile.model_dump()
    name = quote(user["username"], safe="")
    shown["url"] = tile.url.replace(USERNAME, name)
    shown["urls"] = [url.replace(USERNAME, name) for url in tile.urls]
    shown["image"] = icon_image(tile.icon)
    if tile.kind == "widget":
        widget = _library.get(tile.widget)
        if widget is None or not _may_see(widget, user):
            return None
    return shown


def view(user: dict[str, Any]) -> dict[str, Any]:
    """Return a user's dashboards, the widgets they may place, and what they may change.

    A widget is listed by its id, name, type, and size alone; what it shows
    comes from `data_for`.
    """
    mine = [board for board in _boards if _named(board.users, board.groups, user)] or [default_board()]
    shown = []
    for board in mine:
        tiles = [tile for tile in (_tile_view(tile, user) for tile in board.tiles) if tile is not None]
        shown.append({**board.model_dump(exclude={"tiles", "groups", "users"}), "tiles": tiles})
    return {
        "boards": shown,
        "widgets": [
            {"id": widget.id, "name": widget.name, "type": widget.type, "w": widget.w, "h": widget.h}
            for widget in _library.values()
            if _may_see(widget, user)
        ],
        "policy": policy(user),
        "user_types": list(widgets.USER_TYPES),
        "columns": COLUMNS,
    }


def pack_images(names: list[str]) -> dict[str, str]:
    """Return where a page loads each icon pack name from, on this server; a name of no pack is left out."""
    return {name: icon_image(name) for name in names[:200] if isinstance(name, str) and _PACK.fullmatch(name)}


async def data_for(user: dict[str, Any], item: dict[str, Any], fresh: bool = False) -> dict[str, Any]:
    """Return what one widget shows a user.

    Args:
        user: The signed-in user.
        item: `{widget: <id>}` for a library widget, or `{type, config}` for one of the user's own.
        fresh: Read again whatever the cache holds; an administrator's to ask.

    Returns:
        The answer of `widgets.data`, or `{ok: False, error}` for a widget the user may not have.
    """
    if item.get("link"):
        # Whether an administrator's link answers: for a user its dashboard names, and only a link that asks for it.
        board = next((board for board in _boards if board.id == item.get("board")), None)
        tile = next((tile for tile in board.tiles if tile.id == item["link"]), None) if board else None
        if not board or not tile or not tile.status or not (user.get("is_admin") or _named(board.users, board.groups, user)):
            return {"ok": False, "error": "No such link."}
        return await widgets.data("reach", {"url": tile.url.replace(USERNAME, quote(user["username"], safe=""))})
    if item.get("widget"):
        widget = _library.get(str(item["widget"]))
        if widget is None or not _may_see(widget, user):
            return {"ok": False, "error": "No such widget."}
        return await widgets.data(widget.type, widget.config, refresh=widget.refresh, fresh=fresh and bool(user.get("is_admin")))
    kind = str(item.get("type") or "")
    allowed = policy(user)
    if kind not in widgets.USER_TYPES or not allowed["customize"]:
        return {"ok": False, "error": "No such widget."}
    if kind in widgets.USER_FETCH_TYPES and not allowed["feeds"]:
        return {"ok": False, "error": "Your administrator has turned off feeds of your own."}
    try:
        config = widgets.validate(kind, item.get("config"))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return await widgets.data(kind, config, public_only=kind in widgets.USER_FETCH_TYPES)
