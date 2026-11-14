"""The brand the web app wears: the name, logo, accent, wallpaper, app links, and links an administrator gives it.

The brand is a shared object, `branding/branding.yml`, kept beside the
pictures an administrator uploads, `branding/logo.<ext>` and
`branding/wallpaper.<ext>`; every node serves the same brand, and a
deployment can seed it by writing those files. `load` reads them into
`state.branding` and this module's picture cache, the defaults standing in
for whatever is missing or invalid. A picture's address carries the hash of
its bytes, so a browser caches it for a year and still sees a new upload at
once.

A deployment may keep brands of its own for some of its users, the record's
`variants`: each names the host names it is worn at, the groups whose
signed-in members see it, or both, and has pictures of its own beside the
others, `logo.<id>.<ext>` and `wallpaper.<id>.<ext>`, the brand's own
standing in where a variant has none. `choose` picks the brand of a
request, the first variant in the record's order that matches, and every
function below takes the id it returns, empty for the brand itself.

Every page the server serves carries the brand in its head (`brand_page`):
the title, the favicon, an `application-name` meta the translator fills
`{brand}` with, a `sealskin-brand` meta holding the rest as JSON, and a style
block that derives the accent palette from the one color. The pages run no
inline script, so the brand travels as attributes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import io
import json
import logging
import os
import re
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, Response
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from . import logos, persistence
from .models import PROJECT_URL, BrandImage, Branding, BrandingView, BrandLinkView, BrandVariant
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

#: Bytes a picture of each kind may be at most.
IMAGE_LIMITS = {"logo": 1_000_000, "wallpaper": 8_000_000}
#: The picture formats taken, by Pillow's name, with the extension and media type each is kept under.
FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}
#: The default violet, `--accent-primary` of the stylesheets.
DEFAULT_ACCENT = "#8b7cf6"
#: The pink and violet of the default wordmark gradient.
DEFAULT_GRADIENT = ("#e04fb0", "#7c6cf0")
YAML_NAME = "branding.yml"

_TITLE = re.compile(r"<title>([^<]*)</title>")
_ICON_LINK = re.compile(r'<link rel="icon" href="[^"]*">')
_BRAND_TEXT = re.compile(r"(<(?:span|h1)\b[^>]*>)SealSkin(</)")
_DEFAULT_ICON = re.compile(r'(src=")(?:/ui/)?icons/icon128\.png(")')

_VARIANT_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")

#: The uploaded pictures by kind, and by `<kind>.<variant id>` for a variant's own: bytes, media type, hash, and size.
_images: dict[str, dict[str, Any]] = {}


def _dir() -> str:
    """Return the directory of the brand."""
    return settings.branding_path


def _yaml_path() -> str:
    """Return the path of the brand's record."""
    return os.path.join(_dir(), YAML_NAME)


def _inspect(data: bytes) -> tuple[str, str, int, int]:
    """Return the extension, media type, width, and height of a picture, or raise `ValueError`."""
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.verify()
            fmt, size = picture.format, picture.size
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ValueError("The file is not a PNG, JPEG, or WebP picture.") from exc
    if fmt not in FORMATS:
        raise ValueError("The file is not a PNG, JPEG, or WebP picture.")
    extension, media_type = FORMATS[fmt]
    return extension, media_type, size[0], size[1]


def _load_images() -> None:
    """Read the uploaded pictures beside the record into the cache."""
    _images.clear()
    extensions = {ext for ext, _ in FORMATS.values()}
    for name in persistence.list_names(_dir()):
        stem, _, extension = name.rpartition(".")
        kind, _, variant = stem.partition(".")
        if kind not in IMAGE_LIMITS or extension not in extensions or stem in _images or (variant and not _VARIANT_ID.fullmatch(variant)):
            continue
        data = persistence.read_bytes(os.path.join(_dir(), name))
        if not data:
            continue
        try:
            _, media_type, width, height = _inspect(data)
        except ValueError:
            logger.warning("The brand's %s at %s is not a picture; ignoring it.", kind, name)
            continue
        _images[stem] = {
            "data": data,
            "type": media_type,
            "name": name,
            "version": hashlib.sha256(data).hexdigest()[:12],
            "width": width,
            "height": height,
        }


def variant(brand: str) -> BrandVariant | None:
    """Return the variant of an id, or `None` for the brand itself or an id no variant has."""
    return next((item for item in state.branding.variants if item.id == brand), None) if brand else None


def choose(host: str = "", groups: Iterable[str] = ()) -> str:
    """Return the id of the brand a request wears: the first variant its host name or one of its groups matches.

    Args:
        host: The `Host` the request named, with or without a port.
        groups: The groups of the signed-in user, when there is one.

    Returns:
        A variant's id, or `""` for the brand itself.
    """
    name = host.strip().lower().rsplit("@", 1)[-1]
    name = name[1 : name.index("]")] if name.startswith("[") and "]" in name else name.split(":", 1)[0]
    held = set(groups)
    for item in state.branding.variants:
        if any(name == wanted or (wanted.startswith("*.") and name.endswith(wanted[1:])) for wanted in item.hosts):
            return item.id
        if held & set(item.groups):
            return item.id
    return ""


def _record(brand: str) -> Branding:
    """Return the fields a brand is worn with: a variant's own, with the companion apps of the brand itself."""
    item = variant(brand)
    if item is None:
        return state.branding
    return Branding(**item.model_dump(exclude={"id", "label", "hosts", "groups"}), apps=state.branding.apps)


def load() -> None:
    """Read the brand into `state.branding` and the picture cache; defaults stand in for an invalid record."""
    record = None
    try:
        record = persistence.read_yaml(_yaml_path(), None)
    except Exception as exc:  # noqa: BLE001 - a broken file leaves the defaults
        logger.warning("Could not read %s: %s", _yaml_path(), exc)
    branding = Branding()
    if isinstance(record, dict):
        try:
            branding = Branding.model_validate(record)
        except ValidationError as exc:
            logger.warning("Ignoring %s: %s", _yaml_path(), exc)
    state.branding = branding
    for link in [*branding.links, *(link for item in branding.variants for link in item.links)]:
        if link.icon.startswith("https://"):
            logos.local(link.icon)
    try:
        _load_images()
    except Exception as exc:  # noqa: BLE001 - the store may be away; the pages keep the default pictures
        logger.warning("Could not read the brand's pictures: %s", exc)


def save(branding: Branding) -> None:
    """Write the brand's record and take it up, letting go of the pictures of variants it no longer has."""
    persistence.write_yaml_sync(_yaml_path(), branding.model_dump())
    load()
    _prune_images()


def reset() -> None:
    """Remove the record and the pictures, leaving the defaults."""
    for name in persistence.list_names(_dir()):
        persistence.remove(os.path.join(_dir(), name))
    load()


def _own(brand: str) -> str:
    """Return a variant's id as given, or raise 404 for an id no variant has."""
    if brand and variant(brand) is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    return brand


def save_image(kind: str, encoded: str, brand: str = "") -> None:
    """Keep an uploaded picture as a brand's logo or wallpaper.

    Args:
        kind: `logo` or `wallpaper`.
        encoded: The picture, base64 encoded.
        brand: The variant it is for; empty for the brand itself.

    Raises:
        HTTPException: 400 for an unknown kind, bad encoding, a file that is not a picture, or one too large; 404 for an unknown variant.
    """
    if kind not in ("logo", "wallpaper"):
        raise HTTPException(status_code=400, detail="The picture is a logo or a wallpaper.")
    _own(brand)
    try:
        data = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="The picture is not base64 encoded.") from exc
    if not data or len(data) > IMAGE_LIMITS[kind]:
        raise HTTPException(status_code=400, detail=f"A {kind} is at most {IMAGE_LIMITS[kind] // 1_000_000} MB.")
    try:
        extension, _, _, _ = _inspect(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    remove_image(kind, brand, reload=False)
    persistence.write_bytes(os.path.join(_dir(), f"{kind}{'.' + brand if brand else ''}.{extension}"), data)
    load()


def remove_image(kind: str, brand: str = "", reload: bool = True) -> None:
    """Delete a brand's logo or wallpaper, whatever its format."""
    if kind not in ("logo", "wallpaper"):
        raise HTTPException(status_code=400, detail="The picture is a logo or a wallpaper.")
    stem = f"{kind}.{brand}" if brand and _VARIANT_ID.fullmatch(brand) else kind
    for ext, _ in FORMATS.values():
        persistence.remove(os.path.join(_dir(), f"{stem}.{ext}"))
    if reload:
        load()


def _prune_images() -> None:
    """Delete the pictures of variants the record no longer has."""
    known = {item.id for item in state.branding.variants}
    for stem, found in list(_images.items()):
        _, _, owner = stem.partition(".")
        if owner and owner not in known:
            persistence.remove(os.path.join(_dir(), found["name"]))
            _images.pop(stem, None)


def image(kind: str, brand: str = "") -> BrandImage | None:
    """Describe a brand's picture for the pages: a variant's own, else the brand's; `None` where there is neither."""
    own = _images.get(f"{kind}.{brand}") if brand else None
    found = own or _images.get(kind)
    if not found:
        return None
    return BrandImage(
        url=f"/api/branding/{kind}?v={found['version']}" + (f"&b={brand}" if own else ""),
        width=found["width"],
        height=found["height"],
        type=found["type"],
    )


def image_response(kind: str, brand: str = "") -> Response:
    """Answer with a brand's picture, cached for a year under its versioned address.

    Raises:
        HTTPException: 404 where no such picture was uploaded.
    """
    found = (_images.get(f"{kind}.{brand}") if brand and _VARIANT_ID.fullmatch(brand) else None) or _images.get(kind)
    if not found:
        raise HTTPException(status_code=404, detail="No such picture.")
    return Response(found["data"], media_type=found["type"], headers={"Cache-Control": "public, max-age=31536000, immutable"})


def view(brand: str = "") -> BrandingView:
    """Return a brand with its pictures."""
    record = _record(brand)
    item = variant(brand)
    links = [
        BrandLinkView(**link.model_dump(), image=logos.local(link.icon) if link.icon.startswith("https://") else "")
        for link in record.links
    ]
    return BrandingView(
        **record.model_dump(exclude={"links", "variants"}),
        links=links,
        logo=image("logo", brand),
        wallpaper=image("wallpaper", brand),
        is_default=state.branding == Branding() and not _images,
        **(item.model_dump(include={"id", "label", "hosts", "groups"}) if item else {}),
    )


def views() -> list[BrandingView]:
    """Return the brand itself and then every variant, each with its pictures, for the administrator's form."""
    return [view(), *(view(item.id) for item in state.branding.variants)]


def _rgb(color: str) -> tuple[int, int, int]:
    """Split `#rrggbb` into its channels."""
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    """Join channels into `#rrggbb`."""
    return "#" + "".join(f"{max(0, min(255, round(channel))):02x}" for channel in rgb)


def _mix(color: str, other: str, amount: float) -> str:
    """Move `color` toward `other` by `amount` of the way."""
    a, b = _rgb(color), _rgb(other)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(a, b, strict=True)))


def _luminance(color: str) -> float:
    """Return the relative luminance of a color, 0 for black and 1 for white."""
    channels = []
    for channel in _rgb(color):
        c = channel / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def accent_css(accent: str) -> str:
    """Derive the stylesheets' accent palette from one color, as a rule on `:root`.

    The mixes mirror `accentVars` of the dashboard's branding preview, which
    paints the same palette before the brand is saved.
    """
    color = accent.lower()
    r, g, b = _rgb(color)
    light = _luminance(color) > 0.45
    pairs = [
        ("--accent-primary", color),
        ("--accent-primary-hover", _mix(color, "#ffffff", 0.1)),
        ("--accent-text", _mix(color, "#ffffff", 0.35)),
        ("--accent-text-strong", _mix(color, "#ffffff", 0.5)),
        ("--accent-contrast", "#0d1117" if light else "#ffffff"),
        ("--accent-soft", f"rgba({r}, {g}, {b}, 0.14)"),
        ("--accent-border", f"rgba({r}, {g}, {b}, 0.5)"),
        ("--accent-glow", f"rgba({r}, {g}, {b}, 0.35)"),
        ("--accent-gradient", f"linear-gradient(135deg, {color} 0%, {_mix(color, '#000000', 0.18)} 100%)"),
        ("--brand-from", _mix(color, "#ffffff", 0.25)),
        ("--brand-to", color),
    ]
    return ":root{" + ";".join(f"{name}:{value}" for name, value in pairs) + "}"


def client_brand(brand: str = "") -> dict[str, Any]:
    """Return what every page reads from its `sealskin-brand` meta."""
    branding = _record(brand)
    logo = image("logo", brand)
    return {
        "name": branding.name,
        "logo": logo.url if logo else "",
        "logoLink": branding.logo_link,
        "projectLink": branding.logo_link == PROJECT_URL,
        "accent": branding.accent.lower(),
        "apps": [{"client": app.client, "url": app.url} for app in branding.apps if app.enabled],
    }


def brand_page(page: str, brand: str = "") -> str:
    """Return a served page wearing a brand.

    The title and the wordmarks say the name, the favicon and the default
    pictures become the logo where one was uploaded, and the head gains the
    metas and the accent style described in the module docstring.
    """
    branding = _record(brand)
    shown = client_brand(brand)
    name = branding.name
    head = [
        f'<meta name="application-name" content="{html.escape(name, quote=True)}">',
        f"<meta name=\"sealskin-brand\" content='{html.escape(json.dumps(shown, separators=(',', ':')), quote=True)}'>",
    ]
    if branding.accent:
        head.append(f"<style>{accent_css(branding.accent)}</style>")
    if name != "SealSkin":
        page = _TITLE.sub(lambda m: f"<title>{html.escape(m.group(1).replace('SealSkin', name))}</title>", page, count=1)
        page = _BRAND_TEXT.sub(lambda m: f"{m.group(1)}{html.escape(name)}{m.group(2)}", page)
        page = page.replace('alt="SealSkin"', f'alt="{html.escape(name, quote=True)}"')
    if shown["logo"]:
        logo = html.escape(shown["logo"], quote=True)
        page = _ICON_LINK.sub(lambda m: f'<link rel="icon" href="{logo}">', page, count=1)
        page = _DEFAULT_ICON.sub(lambda m: f"{m.group(1)}{logo}{m.group(2)}", page)
    marker = "</head>"
    if marker in page:
        return page.replace(marker, "".join(head) + marker, 1)
    return "".join(head) + page
