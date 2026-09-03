"""Application listing and icons for authenticated users."""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..models import Application
from ..security import EncryptedRoute, verify_token
from ..settings import settings
from ..state import state

logger = logging.getLogger(__name__)
router = APIRouter(route_class=EncryptedRoute)


def user_can_access(app_users: list[str], app_groups: list[str], username: str, group: str) -> bool:
    """Tell whether a user may see an application.

    Args:
        app_users: Usernames allowed by the app (``"all"`` allows everyone).
        app_groups: Groups allowed by the app (``"all"`` allows everyone).
        username: The user.
        group: The user's effective group.

    Returns:
        ``True`` when access is allowed.
    """
    return (
        "all" in app_users or username in app_users or "all" in app_groups or group in app_groups
    )


@router.post("/api/applications", response_model=list[Application])
async def get_applications(user: dict[str, Any] = Depends(verify_token)) -> list[Application]:
    """List the applications the calling user may launch."""
    username = user["username"]
    user_group = user.get("group", "none")
    apps = [
        Application(
            id=app.id,
            name=app.name,
            logo=app.logo,
            home_directories=app.home_directories,
            is_meta_app=app.is_meta_app,
            nvidia_support=app.provider_config.nvidia_support,
            dri3_support=app.provider_config.dri3_support,
            url_support=app.provider_config.url_support,
            extensions=app.provider_config.extensions,
        )
        for app in state.installed_apps.values()
        if user_can_access(app.users, app.groups, username, user_group)
    ]
    return sorted(apps, key=lambda a: a.name.lower())


@router.get("/api/app_icon/{app_id}")
async def get_app_icon(app_id: str, user: dict[str, Any] = Depends(verify_token)) -> dict[str, str]:
    """Return a custom-uploaded app icon as base64 inside JSON."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", app_id):
        raise HTTPException(status_code=400, detail="Invalid application ID.")

    icons_root = os.path.abspath(settings.app_icons_path)
    icon_path = os.path.abspath(os.path.join(icons_root, f"{app_id}.png"))
    if not icon_path.startswith(icons_root + os.sep):
        raise HTTPException(status_code=403, detail="Access denied.")
    if not os.path.exists(icon_path):
        raise HTTPException(status_code=404, detail="Icon not found.")
    try:
        with open(icon_path, "rb") as handle:
            return {"icon_data_b64": base64.b64encode(handle.read()).decode("utf-8")}
    except OSError as exc:
        logger.error("Failed to read icon for app %s: %s", app_id, exc)
        raise HTTPException(status_code=500, detail="Error retrieving icon.") from exc
