"""File manager endpoints for the user's home directories."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import pathlib
import re
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from .. import user_manager
from ..fsutil import safe_rmtree, unique_filename
from ..models import (
    CreateFolderRequest,
    DeleteItemsRequest,
    DeleteStatusResponse,
    DeleteTaskResponse,
    FileChunkResponse,
    FileListResponse,
    FinalizeUploadToDirRequest,
    GenericSuccessMessage,
    LaunchFromStorageRequest,
)
from ..security import (
    EncryptedRoute,
    get_decrypted_request_body,
    verify_persistent_storage_enabled,
    verify_token,
)
from ..settings import settings
from ..state import state
from .uploads import reassemble_file

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/files",
    dependencies=[Depends(verify_persistent_storage_enabled)],
    route_class=EncryptedRoute,
)

CHUNK_SIZE = 2 * 1024 * 1024


def get_validated_path(
    username: str, home_dir: str, sub_path: str, check_existence: bool = True
) -> pathlib.Path:
    """Resolve a path inside a user's home directory, refusing traversal.

    Args:
        username: Owner of the home directory.
        home_dir: Home directory name.
        sub_path: Path relative to the home directory.
        check_existence: Raise 404 when the path does not exist.

    Returns:
        The resolved absolute path.

    Raises:
        HTTPException: 400 for bad names, 403 for traversal or denied access,
            404 when missing.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", home_dir):
        raise HTTPException(status_code=400, detail="Invalid home directory name.")
    if home_dir not in user_manager.get_home_dirs(username):
        raise HTTPException(status_code=403, detail=f"Access to home directory '{home_dir}' denied.")

    base_dir = (pathlib.Path(settings.storage_path) / username / home_dir).resolve()
    if not base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Home directory not found.")

    normalized = os.path.normpath(sub_path).lstrip("/")
    if ".." in normalized.split(os.path.sep):
        raise HTTPException(status_code=403, detail="Directory traversal attempt detected.")
    full_path = (base_dir / normalized).resolve()
    if base_dir not in full_path.parents and full_path != base_dir:
        raise HTTPException(status_code=403, detail="Directory traversal attempt detected.")
    if check_existence and not full_path.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    return full_path


async def _perform_deletion(task_id: str, username: str, home_dir: str, paths: list[str]) -> None:
    """Background worker deleting files and folders."""
    task = state.deletion_tasks[task_id]
    task["status"] = "processing"
    deleted = 0
    try:
        for path in paths:
            validated = get_validated_path(username, home_dir, path)
            if validated.is_dir():
                await asyncio.to_thread(safe_rmtree, str(validated))
            elif validated.is_file():
                await asyncio.to_thread(os.remove, validated)
            deleted += 1
        task.update({"status": "completed", "message": f"Successfully deleted {deleted} items."})
    except HTTPException as exc:
        task.update({"status": "error", "message": exc.detail})
    except Exception as exc:  # noqa: BLE001
        logger.error("Deletion task %s failed: %s", task_id, exc)
        task.update({"status": "error", "message": "An error occurred during deletion."})


@router.get("/download/chunk/{home_dir}", response_model=FileChunkResponse)
async def download_file_chunk(
    home_dir: str,
    path: str = Query(...),
    chunk_index: int = Query(..., ge=0),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, Any]:
    """Return one 2 MiB chunk of a file as base64."""
    validated = get_validated_path(user["username"], home_dir, path)
    if not validated.is_file():
        raise HTTPException(status_code=404, detail="File not found or is a directory.")

    def read_chunk() -> bytes:
        with open(validated, "rb") as handle:
            handle.seek(chunk_index * CHUNK_SIZE)
            return handle.read(CHUNK_SIZE)

    try:
        data = await asyncio.to_thread(read_chunk)
    except OSError as exc:
        logger.error("Error reading chunk for file %s: %s", path, exc)
        raise HTTPException(status_code=500, detail="Error reading file chunk.") from exc
    return {
        "chunk_data_b64": base64.b64encode(data).decode("utf-8"),
        "is_last_chunk": len(data) < CHUNK_SIZE,
    }


@router.post("/create_folder/{home_dir}", response_model=GenericSuccessMessage)
async def create_folder(
    home_dir: str,
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, str]:
    """Create a folder."""
    try:
        req = CreateFolderRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    parent = get_validated_path(user["username"], home_dir, req.path)
    new_folder = parent / req.folder_name
    if new_folder.exists():
        raise HTTPException(status_code=409, detail=f"Folder '{req.folder_name}' already exists.")
    try:
        new_folder.mkdir()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create folder: {exc}") from exc
    return {"message": f"Folder '{req.folder_name}' created successfully."}


@router.post("/delete/{home_dir}", response_model=DeleteTaskResponse)
async def initiate_deletion(
    home_dir: str,
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, str]:
    """Start deleting files and folders in the background."""
    try:
        req = DeleteItemsRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    task_id = str(uuid.uuid4())
    state.deletion_tasks[task_id] = {"status": "pending", "username": user["username"]}
    asyncio.create_task(_perform_deletion(task_id, user["username"], home_dir, req.paths))
    return {"message": "Deletion task started.", "task_id": task_id}


@router.get("/delete_status/{task_id}", response_model=DeleteStatusResponse)
async def check_deletion_status(task_id: str, user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """Return the status of a deletion task."""
    task = state.deletion_tasks.get(task_id)
    if not task or task.get("username") != user["username"]:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.get("status") in ("completed", "error"):
        state.deletion_tasks.pop(task_id, None)
    return task


@router.get("/list/{home_dir}", response_model=FileListResponse)
async def list_files(
    home_dir: str,
    path: str = Query("/"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, Any]:
    """List a directory page by page (folders first)."""
    validated = get_validated_path(user["username"], home_dir, path)
    if not validated.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a valid directory.")
    home_root = pathlib.Path(settings.storage_path) / user["username"] / home_dir

    def scan() -> tuple[int, list[dict[str, Any]]]:
        try:
            entries = sorted(validated.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Error reading directory: {exc}") from exc
        start = (page - 1) * per_page
        items = []
        for entry in entries[start : start + per_page]:
            try:
                stat = entry.stat()
            except OSError:
                continue
            relative = entry.relative_to(home_root)
            items.append(
                {
                    "name": entry.name,
                    "path": "/" if str(relative) == "." else f"/{relative}".replace("\\", "/"),
                    "is_dir": entry.is_dir(),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
        return len(entries), items

    total, items = await asyncio.to_thread(scan)
    return {"items": items, "total": total, "page": page, "per_page": per_page, "path": path}


@router.post("/upload_to_dir/{home_dir}", response_model=GenericSuccessMessage)
async def finalize_upload_to_dir(
    home_dir: str,
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, str]:
    """Move a finished upload into a directory of a home directory."""
    try:
        req = FinalizeUploadToDirRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    dest_dir = get_validated_path(user["username"], home_dir, req.path, check_existence=True)
    if not dest_dir.is_dir():
        raise HTTPException(status_code=400, detail="Destination path is not a valid directory.")

    reassembled_path = await reassemble_file(user["username"], req.upload_id, req.total_chunks)
    safe_filename = os.path.basename(req.filename)
    actual_filename = await asyncio.to_thread(unique_filename, str(dest_dir), safe_filename)
    final_location = dest_dir / actual_filename
    try:
        await asyncio.to_thread(shutil.move, reassembled_path, str(final_location))
        await asyncio.to_thread(os.chmod, str(final_location), 0o644)
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(reassembled_path):
            os.remove(reassembled_path)
        logger.error("Failed to move finalized upload for user '%s': %s", user["username"], exc)
        raise HTTPException(status_code=500, detail="Could not place file in destination.") from exc
    logger.info("User '%s' uploaded '%s' to '%s%s'", user["username"], actual_filename, home_dir, req.path)
    return {"message": "File uploaded successfully."}


@router.post("/launch_from_storage", response_model=dict)
async def launch_from_storage(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, Any]:
    """Return a launch context for a server-side file (the client opens the launcher)."""
    try:
        req = LaunchFromStorageRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    return {"action": "launch", "context": {"action": "server-file", "filename": req.filename}}
