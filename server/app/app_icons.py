"""The icons an administrator uploads for installed applications.

An uploaded icon is kept as a PNG of at most `EDGE` pixels, `<id>.png` under
`app_icons_path`, an object of `store.MOUNTS` that every node of a cluster
reads. The application's `logo` names it `/api/app_icon/<id>`; the routes
that serve it read it by the application's id, so `save` copies the icon of
another application a logo names into the one it is saved for.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re

from . import logos, persistence
from .fsutil import safe_join
from .settings import settings

logger = logging.getLogger(__name__)

#: Where a client finds an application's uploaded icon, followed by the application's id.
ROUTE = "/api/app_icon/"
#: Pixels of the kept icon's longer edge at most.
EDGE = 512
#: Bytes an uploaded picture may be at most.
MAX_BYTES = 4_000_000
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _path(app_id: str) -> str:
    """Return the file an application's icon is kept in.

    Raises:
        ValueError: For an id no file can be named by.
    """
    if not _ID.fullmatch(app_id):
        raise ValueError(f"'{app_id}' is not an application id.")
    return safe_join(settings.app_icons_path, f"{app_id}.png")


def read(app_id: str) -> bytes | None:
    """Return an application's uploaded icon, `None` where it has none."""
    try:
        return persistence.read_bytes(_path(app_id))
    except ValueError:
        return None


def _write(app_id: str, picture: bytes) -> str:
    """Keep `picture` as the application's icon and return the logo that names it."""
    path = _path(app_id)
    # A shared object is overwritten as this node last read it.
    persistence.read_bytes(path)
    persistence.write_bytes(path, picture)
    logger.info("Saved the icon of application %s", app_id)
    return ROUTE + app_id


def save(app_id: str, logo: str | None) -> str | None:
    """Keep the icon a logo carries for an application, and return the logo it is named by.

    Args:
        app_id: The application the icon is for.
        logo: Base64 picture data, bare or as a `data:` URL; the `ROUTE` of
            another application, whose icon is copied; or any other address,
            returned as it is.

    Returns:
        `ROUTE` and `app_id` for a kept icon, otherwise `logo`.

    Raises:
        ValueError: For picture data that is not a picture `logos.to_png` takes, or larger than `MAX_BYTES`.
    """
    if not logo or logo.startswith(("http://", "https://", logos.ROUTE)) or logo == ROUTE + app_id:
        return logo
    if logo.startswith(ROUTE):
        copied = read(logo[len(ROUTE) :])
        return _write(app_id, copied) if copied else ""
    try:
        data = base64.b64decode(logo.split(",", 1)[1] if logo.startswith("data:") else logo, validate=True)
    except (ValueError, binascii.Error):
        return logo
    if len(data) > MAX_BYTES:
        raise ValueError("An icon is at most 4 MB.")
    return _write(app_id, logos.to_png(data, EDGE))


def forget(app_id: str, logo: str | None = None) -> None:
    """Remove an application's uploaded icon, unless `logo` still names it."""
    if logo == ROUTE + app_id:
        return
    try:
        path = _path(app_id)
    except ValueError:
        return
    if persistence.exists(path):
        persistence.remove(path)
        logger.info("Removed the icon of application %s", app_id)


def data_url(app_id: str) -> str | None:
    """Return an application's uploaded icon as a PNG `data:` URL, `None` where it has none."""
    picture = read(app_id)
    return "data:image/png;base64," + base64.b64encode(picture).decode() if picture else None

