"""The address of an application, `/app/<app id>/`, which opens the user's session of it.

Landing on the address signs the user in where nobody is, then takes the
browser to the oldest running session of the application that matches the
address's options, or launches one; the page under `client/src/ui/open.html`
does the asking. The options are the launcher's, as a query: `home`, `gpu`,
`room`, `lang`, `wayland`, and `where`. The address is what a bookmark or an
installed web app keeps, so its manifest names the address, options and all,
as both `id` and `start_url`, and a browser installs each distinct address as
an application of its own.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from ..models import InstalledApp
from ..security import verify_token
from ..settings import settings
from ..state import state
from .applications import user_can_access
from .ui import WEB_APP_CSP, built_page

logger = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

APP_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
#: The options an address carries, in the order its query keeps them.
OPTIONS = ("home", "gpu", "room", "lang", "wayland", "where")
def clean_options(params: Any) -> dict[str, str]:
    """Keep the options an address may carry, with the flags normalized."""
    options: dict[str, str] = {}
    for key in OPTIONS:
        value = str(params.get(key) or "").strip()[:120]
        if key in ("room", "wayland"):
            value = {"1": "1", "true": "1", "0": "0", "false": "0"}.get(value.lower(), "")
        if value:
            options[key] = value
    return options


def address(app_id: str, options: dict[str, str]) -> str:
    """Return the address of an application with `options`, as the manifest and a bookmark keep it."""
    query = urlencode([(key, options[key]) for key in OPTIONS if options.get(key)])
    return f"/app/{app_id}/" + (f"?{query}" if query else "")


def manifest_for(app: InstalledApp, options: dict[str, str], icon: str) -> dict[str, Any]:
    """Describe an application with `options` for installing it as a web app of its own.

    Args:
        app: The application.
        options: The address's options (see `clean_options`).
        icon: The path the icon is served at.
    """
    start = address(app.id, clean_options(options))
    return {
        "name": app.name,
        "short_name": app.name[:30],
        "id": start,
        "start_url": start,
        "scope": "/",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#0d1117",
        "icons": [{"src": icon, "sizes": "512x512", "type": "image/png", "purpose": "any"}],
    }


def icon_response(app: InstalledApp) -> Response:
    """Answer with an application's icon: its uploaded one, or a redirect to the one its store names."""
    logo = app.logo or ""
    if logo.startswith(("http://", "https://")):
        return RedirectResponse(logo, status_code=302)
    if logo.startswith("/api/app_icon/") and APP_ID.fullmatch(app.id):
        path = os.path.abspath(os.path.join(settings.app_icons_path, f"{app.id}.png"))
        if path.startswith(os.path.abspath(settings.app_icons_path) + os.sep) and os.path.isfile(path):
            return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    fallback = os.path.join(settings.ui_path, "icons", "icon128.png")
    if os.path.isfile(fallback):
        return FileResponse(fallback, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    raise HTTPException(status_code=404, detail="Icon not found.")


def _app(app_id: str) -> InstalledApp:
    """Return the installed application an address names."""
    app = state.installed_apps.get(app_id) if APP_ID.fullmatch(app_id) else None
    if not app:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


def _open_to(app: InstalledApp, user: dict[str, Any]) -> InstalledApp:
    """Return `app` when `user` may launch it."""
    if user.get("is_admin") or user_can_access(app.users, app.groups, user["username"], user.get("groups") or []):
        return app
    raise HTTPException(status_code=404, detail="Application not found.")


@router.get("/app/{app_id}")
async def app_address_slash(app_id: str, request: Request) -> RedirectResponse:
    """Send an address written without its trailing slash to the one with it, options kept."""
    _app(app_id)
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/app/{app_id}/{query}", status_code=308)


@router.get("/app/{app_id}/", response_class=HTMLResponse)
async def app_address(app_id: str) -> HTMLResponse:
    """Serve the page that opens the application; it tells the visitor itself when the application is not theirs."""
    _app(app_id)
    return HTMLResponse(
        built_page("open.html"),
        headers={"Cache-Control": "no-cache", "Content-Security-Policy": WEB_APP_CSP},
    )


@router.get("/app/{app_id}/manifest.json")
async def app_manifest(app_id: str, request: Request, user: dict[str, Any] = Depends(verify_token)) -> JSONResponse:
    """Describe the application with the address's options for installing it."""
    app = _open_to(_app(app_id), user)
    return JSONResponse(
        manifest_for(app, clean_options(request.query_params), icon=f"/app/{app_id}/icon.png"),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/app/{app_id}/icon.png")
async def app_icon(app_id: str) -> Response:
    """Answer the application's icon, which an installing browser may ask for without the sign-in."""
    return icon_response(_app(app_id))
