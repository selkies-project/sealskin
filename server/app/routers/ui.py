"""Serving the built web UI, the template schema and the landing page."""

from __future__ import annotations

import logging
import os
from typing import Any

import yaml
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from starlette.staticfiles import StaticFiles

from ..models import TemplateSchemaResponse, TemplateSchemaSetting, UiManifest
from ..settings import settings
from ..version import __version__

logger = logging.getLogger(__name__)
router = APIRouter()

BRIDGE_VERSION = 1
NO_CACHE_SUFFIXES = (".html", ".json")


class UiStaticFiles(StaticFiles):
    """Static files with cache headers suited to content-hashed builds.

    HTML entry points and JSON manifests are served with ``no-cache`` so a new
    build is picked up immediately; every other asset carries a content hash
    in its name and is cached for a year.
    """

    def file_response(self, full_path: Any, stat_result: os.stat_result, scope: Any, status_code: int = 200) -> Response:
        """Add ``Cache-Control`` to the response Starlette builds."""
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(NO_CACHE_SUFFIXES):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_ui(app: FastAPI) -> None:
    """Mount the built UI at ``/ui`` when the directory exists.

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
    app.mount("/ui", UiStaticFiles(directory=settings.ui_path), name="ui")
    logger.info("Serving web UI from %s", settings.ui_path)


def load_template_schema() -> list[TemplateSchemaSetting]:
    """Read and validate the template schema file.

    Returns:
        The list of settings, or an empty list when the file is missing.

    Raises:
        ValueError: If the file is not a mapping with a ``settings`` list.
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
