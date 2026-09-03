"""Session launch endpoints."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ..launch import launch_application
from ..models import (
    LaunchRequestFile,
    LaunchRequestFilePath,
    LaunchRequestSimple,
    LaunchRequestURL,
    LaunchResponse,
)
from ..security import (
    EncryptedRoute,
    get_decrypted_request_body,
    verify_persistent_storage_enabled,
    verify_token,
)
from ..settings import settings
from .uploads import reassemble_file

logger = logging.getLogger(__name__)
router = APIRouter(route_class=EncryptedRoute)


@router.post("/api/launch/simple", response_model=LaunchResponse)
async def launch_simple(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    auth_user: dict[str, Any] = Depends(verify_token),
) -> dict[str, str]:
    """Launch an application with no context."""
    try:
        req = LaunchRequestSimple(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    return await launch_application(
        req.application_id,
        auth_user["username"],
        auth_user["effective_settings"],
        req.home_name,
        {},
        req.language,
        req.selected_gpu,
        launch_in_room_mode=req.launch_in_room_mode,
        wayland_mode=req.wayland_mode,
        timezone=req.timezone,
    )


@router.post("/api/launch/url", response_model=LaunchResponse)
async def launch_url(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    auth_user: dict[str, Any] = Depends(verify_token),
) -> dict[str, str]:
    """Launch an application that opens a URL."""
    try:
        req = LaunchRequestURL(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    return await launch_application(
        req.application_id,
        auth_user["username"],
        auth_user["effective_settings"],
        req.home_name,
        {"SEALSKIN_URL": req.url},
        req.language,
        req.selected_gpu,
        launch_in_room_mode=req.launch_in_room_mode,
        wayland_mode=req.wayland_mode,
        timezone=req.timezone,
    )


@router.post("/api/launch/file", response_model=LaunchResponse)
async def launch_file(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    auth_user: dict[str, Any] = Depends(verify_token),
) -> dict[str, str]:
    """Launch an application with a previously uploaded file."""
    try:
        req = LaunchRequestFile(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc

    reassembled_path = await reassemble_file(auth_user["username"], req.upload_id, req.total_chunks)
    try:
        with open(reassembled_path, "rb") as handle:
            file_bytes = handle.read()
    finally:
        if os.path.exists(reassembled_path):
            os.remove(reassembled_path)

    return await launch_application(
        req.application_id,
        auth_user["username"],
        auth_user["effective_settings"],
        req.home_name,
        {},
        req.language,
        req.selected_gpu,
        file_bytes,
        os.path.basename(req.filename),
        req.open_file_on_launch,
        launch_in_room_mode=req.launch_in_room_mode,
        wayland_mode=req.wayland_mode,
        timezone=req.timezone,
    )


@router.post("/api/launch/file_path", response_model=LaunchResponse)
async def launch_file_path(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    auth_user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, str]:
    """Launch an application with a file from the user's shared files."""
    try:
        req = LaunchRequestFilePath(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc

    if req.home_name and req.home_name.lower() == "cleanroom":
        raise HTTPException(
            status_code=400, detail="Cannot open a server-side file in 'Cleanroom' mode."
        )

    username = auth_user["username"]
    shared_files_path = os.path.abspath(
        os.path.join(settings.storage_path, username, "_sealskin_shared_files")
    )
    safe_filename = os.path.basename(req.filename)
    if not os.path.exists(os.path.join(shared_files_path, safe_filename)):
        raise HTTPException(status_code=404, detail=f"File '{safe_filename}' not found.")

    env_vars = {
        "SEALSKIN_FILE": os.path.join(
            settings.container_config_path, "Desktop", "files", safe_filename
        )
    }
    return await launch_application(
        req.application_id,
        username,
        auth_user["effective_settings"],
        req.home_name,
        env_vars,
        req.language,
        req.selected_gpu,
        wayland_mode=req.wayland_mode,
        timezone=req.timezone,
    )
