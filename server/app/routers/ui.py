"""Serving the built web UI, the web app's manifest, the template schema, and the landing page."""

from __future__ import annotations

import logging
import mimetypes
import os
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from ..models import TemplateSchemaResponse, TemplateSchemaSetting, UiManifest
from ..settings import settings
from ..state import state
from ..version import __version__

logger = logging.getLogger(__name__)
router = APIRouter()

BRIDGE_VERSION = 1
NO_CACHE_SUFFIXES = (".html", ".json", "/sw.js")
WEB_APP_CSP = "frame-ancestors 'none'; script-src 'self'; object-src 'none'; base-uri 'none'"


class UiStaticFiles(StaticFiles):
    """Static files with cache headers suited to content-hashed builds.

    HTML entry points, JSON manifests, and the service worker are served with
    `no-cache` so a new build is picked up immediately; every other asset
    carries a content hash in its name and is cached for a year. The web app's
    page holds the unlocked private key, so it runs only its own scripts, is
    never framed, and gives a page that opens it, such as a session's, no
    handle on it.
    """

    def file_response(self, full_path: Any, stat_result: os.stat_result, scope: Any, status_code: int = 200) -> Response:
        """Add `Cache-Control`, and the web app's isolation, to the response Starlette builds."""
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(NO_CACHE_SUFFIXES):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        if os.path.basename(str(full_path)) == "index.html":
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
            response.headers["Content-Security-Policy"] = WEB_APP_CSP
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


def load_template_schema() -> list[TemplateSchemaSetting]:
    """Read and validate the template schema file.

    Returns:
        The list of settings, or an empty list when the file is missing.

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
    return [TemplateSchemaSetting(**entry) for entry in entries]


@router.get("/api/ui/template_schema", response_model=TemplateSchemaResponse)
async def get_template_schema() -> dict[str, Any]:
    """Return the environment variable definitions for the template editor."""
    try:
        return {"settings": load_template_schema()}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load template schema: %s", exc)
        raise HTTPException(status_code=500, detail="Template schema is invalid.") from exc


@router.get("/ui/manifest.webmanifest", include_in_schema=False)
async def web_app_manifest() -> JSONResponse:
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
    manifest: dict[str, Any] = {
        "name": "SealSkin",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#0d1117",
        "icons": [
            {"src": "icons/icon128.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icons/logo.png", "sizes": "1024x1024", "type": "image/png"},
        ],
        "share_target": {
            "action": "share",
            "method": "POST",
            "enctype": "multipart/form-data",
            "params": {"title": "title", "text": "text", "url": "url", "files": [{"name": "file", "accept": ["*/*"]}]},
        },
        "protocol_handlers": [{"protocol": "web+sealskin", "url": "./?url=%s"}],
    }
    if accept:
        manifest["file_handlers"] = [{"action": "./", "accept": accept}]
    return JSONResponse(manifest, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/ui/opensearch.xml", include_in_schema=False)
async def opensearch_description(request: Request) -> Response:
    """Let the browser add a search engine that opens the terms in a session."""
    app = str(request.url_for("ui", path="/"))
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        "<ShortName>SealSkin</ShortName>"
        "<Description>Search in an isolated SealSkin session</Description>"
        "<InputEncoding>UTF-8</InputEncoding>"
        f'<Image width="192" height="192" type="image/png">{escape(app)}icons/icon128.png</Image>'
        f'<Url type="text/html" method="get" template={quoteattr(app + "?q={searchTerms}")}/>'
        "</OpenSearchDescription>"
    )
    return Response(body, media_type="application/opensearchdescription+xml")


@router.get("/api/ui/version", response_model=UiManifest)
async def get_ui_version() -> dict[str, Any]:
    """Return the server version and the bridge protocol it speaks."""
    return {"version": __version__, "bridge": BRIDGE_VERSION}


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def read_root() -> HTMLResponse:
    """Serve the landing page."""
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "index.html")
    if os.path.exists(html_path):
        with open(html_path, encoding="utf-8") as handle:
            return HTMLResponse(content=handle.read())
    return HTMLResponse(content="<h1>SealSkin Server</h1>", status_code=404)
