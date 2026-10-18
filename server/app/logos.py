"""The pictures of applications and links, served by this node so that no page loads one from elsewhere.

A store names an application's logo by an address on the web, a PRoot Apps
remote its icons the same way, and an administrator may give a home link an
`https` picture. Such an address never reaches a client: wherever the server
hands one out, `local` turns it into `/api/logos/<name>`, `<name>` being the
hash of the address, and remembers the address under that name. The picture
behind a name is fetched by this node when a store is refreshed (`prefetch`)
or the first time a client asks for it, and kept under
`app_store_cache_path/logos`, each node its own copy. A picture is served
with caching headers and a content security policy that keeps a script in an
SVG from running on the web app's origin, since a store, not an
administrator, chose it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re

import httpx
from fastapi import Request, Response

from .fsutil import safe_join
from .settings import settings

logger = logging.getLogger(__name__)

#: Where a client finds a picture, followed by its name.
ROUTE = "/api/logos/"
#: Bytes a picture may be at most.
MAX_BYTES = 2_000_000
#: The picture formats taken, by media type, with the extension each is kept under.
TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/avif": "avif",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
}
#: Seconds a browser keeps a picture before asking again, which an `ETag` answers cheaply.
MAX_AGE = 86400
#: Pictures fetched at once by `prefetch`.
PARALLEL = 6
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_HEADERS = {
    "Cache-Control": f"public, max-age={MAX_AGE}",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}
_NAME = re.compile(r"^[0-9a-f]{32}$")
_MEDIA_TYPES = {ext: media for media, ext in TYPES.items()}

#: The address behind every name handed out since the server started.
_known: dict[str, str] = {}
_locks: dict[str, asyncio.Lock] = {}
#: Set by a test to answer the fetches itself.
_transport: httpx.AsyncBaseTransport | None = None


def name_of(address: str) -> str:
    """Return the name a picture at `address` is served under."""
    return hashlib.sha256(address.encode("utf-8")).hexdigest()[:32]


def local(address: str) -> str:
    """Return the address a client loads a picture from, remembering a web address under its name.

    Anything but an `http` or `https` address, such as an uploaded icon's path
    or nothing, is returned as it is.
    """
    if not address.startswith(("http://", "https://")):
        return address
    name = name_of(address)
    _known[name] = address
    return ROUTE + name


def _dir() -> str:
    return os.path.join(settings.app_store_cache_path, "logos")


def _find(name: str) -> tuple[str, str] | None:
    """Return the file and media type of a cached picture, or `None` where there is none."""
    for ext, media in _MEDIA_TYPES.items():
        path = safe_join(_dir(), f"{name}.{ext}")
        if os.path.exists(path):
            return path, media
    return None


async def _download(client: httpx.AsyncClient, address: str) -> tuple[bytes, str] | None:
    """Fetch a picture, or return `None` when what answers is not a picture this server keeps."""
    async with client.stream("GET", address) as response:
        if response.status_code != 200:
            logger.warning("Logo %s answered %s", address, response.status_code)
            return None
        media = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if media not in TYPES:
            logger.warning("Logo %s is %s, not a picture", address, media or "untyped")
            return None
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                logger.warning("Logo %s is larger than %d bytes", address, MAX_BYTES)
                return None
    return bytes(data), media


async def fetch(name: str) -> tuple[bytes, str] | None:
    """Return the bytes and media type of a picture, fetching it into the cache when it is not there yet.

    Returns:
        `None` for a name never handed out or a picture that could not be had.
    """
    address = _known.get(name)
    if not address:
        return None
    lock = _locks.setdefault(name, asyncio.Lock())
    async with lock:
        found = _find(name)
        if found:
            with open(found[0], "rb") as handle:
                return handle.read(), found[1]
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, transport=_transport) as client:
                picture = await _download(client, address)
        except httpx.HTTPError as exc:
            logger.warning("Logo %s not fetched: %s", address, exc)
            return None
        if not picture:
            return None
        data, media = picture
        path = safe_join(_dir(), f"{name}.{TYPES[media]}")
        try:
            os.makedirs(_dir(), exist_ok=True)
            with open(path + ".part", "wb") as handle:
                handle.write(data)
            os.replace(path + ".part", path)
        except OSError as exc:
            logger.warning("Logo %s not kept: %s", address, exc)
        return data, media


async def prefetch() -> None:
    """Fetch every known picture that is not cached yet, and drop the files of addresses no longer handed out."""
    missing = [name for name in list(_known) if not _find(name)]
    gate = asyncio.Semaphore(PARALLEL)

    async def one(name: str) -> bool:
        async with gate:
            return await fetch(name) is not None

    if missing:
        had = sum(await asyncio.gather(*(one(name) for name in missing)))
        logger.info("Fetched %d of %d logos.", had, len(missing))
    try:
        for file in os.listdir(_dir()):
            if file.split(".")[0] not in _known:
                os.remove(os.path.join(_dir(), file))
    except OSError:
        pass


async def serve(name: str, request: Request) -> Response:
    """Answer a client's request for a picture by name, 404 for one this server never handed out."""
    if not _NAME.match(name):
        return Response(status_code=404)
    picture = await fetch(name)
    if not picture:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    data, media = picture
    etag = f'"{hashlib.sha256(data).hexdigest()[:16]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={**_HEADERS, "ETag": etag})
    return Response(data, media_type=media, headers={**_HEADERS, "ETag": etag})


def reset() -> None:
    """Forget every address, for a test."""
    _known.clear()
    _locks.clear()
