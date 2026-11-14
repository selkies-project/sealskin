"""Serving the web app at `/`, the built UI under `/ui/`, the web app's manifest, and the template schema.

The web app's page is served at the root, so the server's address is the
web app's and a sign-in lands there; its assets, the pages the extension and
the mobile app frame, and its service worker stay under `/ui/`, where the
worker's scope keeps it off the sessions of the origin. `/ui/` itself sends
the browser to `/`, so an older link or a provider's return still arrives.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import posixpath
import re
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

from .. import branding, sso, user_manager
from ..models import (
    TemplateSchemaResponse,
    TemplateSchemaSection,
    TemplateSchemaSetting,
    UiManifest,
)
from ..settings import settings
from ..state import state
from ..version import __version__

logger = logging.getLogger(__name__)
router = APIRouter()

BRIDGE_VERSION = 1
NO_CACHE_SUFFIXES = (".html", ".json", "/sw.js")
#: The web app runs only its own scripts, is never framed, and sets its own `<base>` alone.
WEB_APP_CSP = "frame-ancestors 'none'; script-src 'self'; object-src 'none'; base-uri 'self'"
#: The pages the web app and the shells frame run only their own scripts too.
PAGE_CSP = "script-src 'self'; object-src 'none'; base-uri 'self'"
#: The room's page builds its workers and audio worklets from its own text, which the browser sees as `blob:` scripts.
ROOM_CSP = "script-src 'self' blob:; worker-src 'self' blob:; object-src 'none'; base-uri 'self'"
#: The page a dashboard's frame widget is written into: an origin of its own with no way to the page around it,
#: free to run the administrator's script and load from anywhere.
WIDGET_FRAME_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox; "
    "default-src * data: blob: 'unsafe-inline' 'unsafe-eval'; base-uri 'none'"
)
_ASSET_RE = re.compile(r'(?P<attr>\b(?:src|href))="(?P<url>[^"]+)"')


def absolutize(html: str, base: str) -> str:
    """Point the relative `src` and `href` values of a built page at `base`, where its assets are served.

    A value written `./<name>` is left as it is: it names what is served
    beside the page itself, such as its manifest.
    """

    def rewrite(match: re.Match[str]) -> str:
        url = match.group("url")
        if url.startswith(("/", "./", "http://", "https://", "data:", "about:", "#", "?")):
            return match.group(0)
        return f'{match.group("attr")}="{posixpath.normpath(posixpath.join(base, url))}"'

    return _ASSET_RE.sub(rewrite, html)


async def brand_of(request: Request) -> str:
    """Return the id of the brand a page request wears (see `branding.choose`).

    The host name is the one the browser asked for, and the groups are those
    of the web sign-in its cookie names, when there is one; the pages a
    companion app frames carry no sign-in, and wear the brand of their host
    name.
    """
    groups: Any = ()
    token = request.cookies.get(sso.SESSION_COOKIE)
    if token and any(item.groups for item in state.branding.variants):
        try:
            signed_in = await sso.current(sso.session_id(token))
            if signed_in:
                groups = user_manager.get_effective_settings(signed_in["username"], signed_in.get("groups") or ()).get("groups") or ()
        except Exception:  # noqa: BLE001 - a sign-in that cannot be read leaves the host name to pick the brand
            groups = ()
    return branding.choose(request.headers.get("host", ""), groups)


def built_page(name: str, base: str = "/ui/", brand: str = "") -> str:
    """Return a page of the built UI wearing a brand, with its assets pointed at `base`.

    Raises:
        HTTPException: 404 when the UI is not built.
    """
    try:
        with open(os.path.join(settings.ui_path, name), encoding="utf-8") as handle:
            return branding.brand_page(absolutize(handle.read(), base), brand)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="The web UI is not built.") from exc


class UiStaticFiles(StaticFiles):
    """Static files with cache headers suited to content-hashed builds.

    HTML entry points, JSON manifests, and the service worker are served with
    `no-cache` so a new build is picked up immediately; every other asset
    carries a content hash in its name and is cached for a year. A page is
    read and served wearing the brand (`branding.brand_page`). The web app's
    page holds the unlocked private key, so it runs only its own scripts, is
    never framed, and gives a page that opens it, such as a session's, no
    handle on it. Every other page runs only its own scripts as well, the
    room's with the workers it builds, and the page of a frame widget is
    sandboxed into an origin of its own.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        """Note the brand the request wears before the file is answered with."""
        scope["sealskin.brand"] = await brand_of(Request(scope))
        return await super().get_response(path, scope)

    def file_response(self, full_path: Any, stat_result: os.stat_result, scope: Any, status_code: int = 200) -> Response:
        """Add `Cache-Control`, the brand, and the web app's isolation to the response Starlette builds."""
        if str(full_path).endswith(".html"):
            with open(full_path, encoding="utf-8") as handle:
                response: Response = HTMLResponse(branding.brand_page(handle.read(), scope.get("sealskin.brand", "")), status_code=status_code)
        else:
            response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(NO_CACHE_SUFFIXES):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        name = os.path.basename(str(full_path))
        if name in ("index.html", "receive.html", "launching.html"):
            response.headers["Content-Security-Policy"] = WEB_APP_CSP
        elif name == "widget-frame.html":
            response.headers["Content-Security-Policy"] = WIDGET_FRAME_CSP
        elif name == "room.html":
            response.headers["Content-Security-Policy"] = ROOM_CSP
        elif name.endswith(".html"):
            response.headers["Content-Security-Policy"] = PAGE_CSP
        # Only the app page severs its opener; receive.html keeps the one the
        # pick bookmarklet opened it with, to take the fetched file from it.
        if name == "index.html":
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        return response


def mount_ui(app: FastAPI) -> None:
    """Mount the built UI at `/ui` when the directory exists.

    Args:
        app: The FastAPI application.
    """
    if not os.path.isdir(settings.ui_path):
        logger.error(
            "Web UI directory '%s' does not exist. Build the client (cd client && npm run build) "
            "or set SEALSKIN_UI_PATH. /ui will return 404.",
            settings.ui_path,
        )
        return
    app.mount("/ui", UiStaticFiles(directory=settings.ui_path, html=True), name="ui")
    logger.info("Serving web UI from %s", settings.ui_path)


def load_template_schema() -> TemplateSchemaResponse:
    """Read and validate the template schema file.

    Returns:
        The editor's sections and settings, both empty when the file is missing.

    Raises:
        ValueError: If the file is not a mapping with a `settings` list.
    """
    data = {}
    if os.path.exists(settings.template_schema_path):
        with open(settings.template_schema_path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    entries = data.get("settings") if isinstance(data, dict) else None
    if entries is None:
        raise ValueError("template_schema.yml must contain a 'settings' list.")
    return TemplateSchemaResponse(
        sections=[TemplateSchemaSection(**section) for section in data.get("sections") or []],
        settings=[TemplateSchemaSetting(**entry) for entry in entries],
    )


@router.get("/api/ui/template_schema", response_model=TemplateSchemaResponse)
async def get_template_schema() -> dict[str, Any]:
    """Return the environment variable definitions for the template editor."""
    try:
        return load_template_schema().model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load template schema: %s", exc)
        raise HTTPException(status_code=500, detail="Template schema is invalid.") from exc


@router.get("/ui/manifest.webmanifest", include_in_schema=False)
async def web_app_manifest(request: Request) -> JSONResponse:
    """Describe the web app for installing it, sharing to it, and opening files and links with it.

    The file handlers offer the extensions installed applications open, each
    under its MIME type. Linux desktops register that pair, so an extension
    with no known type is left out rather than filed under the wrong one.
    """
    accept: dict[str, list[str]] = {}
    extensions = {ext.strip(".").lower() for app in state.installed_apps.values() for ext in app.provider_config.extensions}
    for extension in sorted(filter(None, extensions)):
        mime = mimetypes.guess_type(f"file.{extension}")[0]
        if mime:
            accept.setdefault(mime, []).append(f".{extension}")
    brand = await brand_of(request)
    logo = branding.image("logo", brand)
    icons = (
        [{"src": logo.url, "sizes": f"{logo.width}x{logo.height}", "type": logo.type}]
        if logo
        else [
            {"src": "icons/icon128.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icons/logo.png", "sizes": "1024x1024", "type": "image/png"},
        ]
    )
    manifest: dict[str, Any] = {
        "name": branding.view(brand).name,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#0d1117",
        "icons": icons,
        "share_target": {
            "action": "share",
            "method": "POST",
            "enctype": "multipart/form-data",
            "params": {"title": "title", "text": "text", "url": "url", "files": [{"name": "file", "accept": ["*/*"]}]},
        },
        "protocol_handlers": [{"protocol": "web+sealskin", "url": "/?url=%s"}],
    }
    if accept:
        manifest["file_handlers"] = [{"action": "/", "accept": accept}]
    return JSONResponse(manifest, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/ui/opensearch.xml", include_in_schema=False)
async def opensearch_description(request: Request) -> Response:
    """Let the browser add a search engine that opens the terms in a session.

    The template names `public_url` where it is set, and the address the
    browser used otherwise: behind a reverse proxy the request need not carry
    the browser's port.
    """
    public = settings.public_url.rstrip("/")
    app = f"{public}/" if public else str(request.base_url)
    brand = await brand_of(request)
    name = branding.view(brand).name
    logo = branding.image("logo", brand)
    picture = (
        f'<Image width="{logo.width}" height="{logo.height}" type="{logo.type}">{escape(app.rstrip("/") + logo.url)}</Image>'
        if logo
        else f'<Image width="192" height="192" type="image/png">{escape(app)}ui/icons/icon128.png</Image>'
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        f"<ShortName>{escape(name)}</ShortName>"
        f"<Description>Search in an isolated {escape(name)} session</Description>"
        "<InputEncoding>UTF-8</InputEncoding>"
        f"{picture}"
        f'<Url type="text/html" method="get" template={quoteattr(app + "?q={searchTerms}")}/>'
        "</OpenSearchDescription>"
    )
    return Response(body, media_type="application/opensearchdescription+xml")


@router.get("/api/ui/version", response_model=UiManifest)
async def get_ui_version() -> dict[str, Any]:
    """Return the server version and the bridge protocol it speaks."""
    return {"version": __version__, "bridge": BRIDGE_VERSION}


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web_app(request: Request) -> HTMLResponse:
    """Serve the web app's page, with the isolation its built copy under `/ui/` carries."""
    return HTMLResponse(
        built_page("index.html", brand=await brand_of(request)),
        headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": WEB_APP_CSP,
            "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
        },
    )


@router.get("/ui/", include_in_schema=False)
@router.get("/ui/index.html", include_in_schema=False)
async def web_app_moved(request: Request) -> RedirectResponse:
    """Send the web app's old address to `/`, query kept; a browser keeps the fragment too."""
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/{query}", status_code=308)
