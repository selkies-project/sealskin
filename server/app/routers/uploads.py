"""Chunked uploads, scoped to the uploading user.

Uploads live under `<upload_dir>/<username>/<upload uuid>/` and every
consumer must pass the authenticated username, so one user can never read or
complete another user's upload.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from .. import user_manager
from ..fsutil import unique_filename
from ..models import (
    UploadChunkRequest,
    UploadInitiateRequest,
    UploadInitiateResponse,
    UploadToStorageRequest,
)
from ..security import (
    EncryptedRoute,
    get_decrypted_request_body,
    verify_persistent_storage_enabled,
    verify_token,
)
from ..settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/upload",
    dependencies=[Depends(verify_token)],
    route_class=EncryptedRoute,
)

_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_upload_id(upload_id: str) -> str:
    """Ensure an upload id is a lower-case UUID.

    Args:
        upload_id: Client-supplied id.

    Returns:
        The id unchanged.

    Raises:
        HTTPException: 400 when the id is malformed.
    """
    if not upload_id or not _UPLOAD_ID_RE.match(upload_id):
        raise HTTPException(status_code=400, detail="Invalid upload id.")
    return upload_id


def user_upload_root(username: str) -> str:
    """Return the upload directory of a user (created on demand).

    Raises:
        HTTPException: 400 when the username is not filesystem safe.
    """
    if not _USERNAME_RE.match(username or ""):
        raise HTTPException(status_code=400, detail="Invalid username.")
    root = os.path.join(settings.upload_dir, username)
    os.makedirs(root, exist_ok=True, mode=0o700)
    return root


def upload_path(username: str, upload_id: str) -> str:
    """Return the directory of one upload after validating ownership inputs."""
    return os.path.join(user_upload_root(username), validate_upload_id(upload_id))


async def reassemble_file(username: str, upload_id: str, total_chunks: int) -> str:
    """Concatenate the chunks of an upload into a temporary file.

    Args:
        username: Owner of the upload.
        upload_id: Upload id.
        total_chunks: Expected number of chunks.

    Returns:
        Path of the reassembled temporary file (caller removes it).

    Raises:
        HTTPException: 404 when the upload is unknown, 400 when a chunk is
            missing, 500 on I/O errors.
    """
    directory = upload_path(username, upload_id)
    if not os.path.isdir(directory):
        raise HTTPException(status_code=404, detail="Upload session not found.")
    for index in range(total_chunks):
        if not os.path.exists(os.path.join(directory, f"chunk_{index}")):
            raise HTTPException(status_code=400, detail=f"Missing chunk {index} for upload.")

    fd, temp_path = tempfile.mkstemp(dir=user_upload_root(username), prefix=f"{upload_id}-")
    try:

        def concatenate() -> None:
            with os.fdopen(fd, "wb") as final_file:
                for index in range(total_chunks):
                    with open(os.path.join(directory, f"chunk_{index}"), "rb") as chunk:
                        shutil.copyfileobj(chunk, final_file)

        await asyncio.to_thread(concatenate)
        await asyncio.to_thread(shutil.rmtree, directory, ignore_errors=True)
        return temp_path
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(temp_path):
            os.remove(temp_path)
        await asyncio.to_thread(shutil.rmtree, directory, ignore_errors=True)
        logger.error("Failed to reassemble file for upload %s: %s", upload_id, exc)
        raise HTTPException(status_code=500, detail="Failed to reassemble file.") from exc


@router.post("/initiate", response_model=UploadInitiateResponse)
async def upload_initiate(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_token),
) -> dict[str, str]:
    """Start a chunked upload and return its id."""
    try:
        req = UploadInitiateRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    upload_id = str(uuid.uuid4())
    directory = upload_path(user["username"], upload_id)
    os.makedirs(directory, exist_ok=True, mode=0o700)
    with open(os.path.join(directory, "metadata.json"), "w", encoding="utf-8") as handle:
        json.dump({"filename": req.filename, "size": req.total_size, "started": time.time()}, handle)
    return {"upload_id": upload_id}


@router.post("/chunk")
async def upload_chunk(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_token),
) -> dict[str, Any]:
    """Store one chunk of an upload."""
    try:
        req = UploadChunkRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    if req.chunk_index < 0:
        raise HTTPException(status_code=400, detail="Invalid chunk index.")
    directory = upload_path(user["username"], req.upload_id)
    if not os.path.isdir(directory):
        raise HTTPException(status_code=404, detail="Upload session not found.")
    try:
        data = base64.b64decode(req.chunk_data_b64)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Base64 chunk data: {exc}") from exc

    def write() -> None:
        with open(os.path.join(directory, f"chunk_{req.chunk_index}"), "wb") as handle:
            handle.write(data)

    await asyncio.to_thread(write)
    return {"status": "ok", "chunk_index": req.chunk_index}


@router.post("/to_storage", dependencies=[Depends(verify_persistent_storage_enabled)])
async def upload_to_storage(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, str]:
    """Finalise an upload into the user's shared files directory."""
    try:
        req = UploadToStorageRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    username = user["username"]
    if req.home_name not in user_manager.get_home_dirs(username):
        raise HTTPException(
            status_code=404, detail=f"Home directory '{req.home_name}' not found for user."
        )

    reassembled_path = await reassemble_file(username, req.upload_id, req.total_chunks)
    safe_filename = os.path.basename(req.filename)
    dest_dir = os.path.join(settings.storage_path, username, "_sealskin_shared_files")
    await asyncio.to_thread(os.makedirs, dest_dir, exist_ok=True, mode=0o755)
    actual_filename = await asyncio.to_thread(unique_filename, dest_dir, safe_filename)
    file_location = os.path.join(dest_dir, actual_filename)
    try:
        await asyncio.to_thread(shutil.move, reassembled_path, file_location)
        await asyncio.to_thread(os.chmod, file_location, 0o644)
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(reassembled_path):
            os.remove(reassembled_path)
        logger.error("Failed to move reassembled file to storage for user '%s': %s", username, exc)
        raise HTTPException(status_code=500, detail="Could not place file in session storage.") from exc

    logger.info(
        "User '%s' uploaded file '%s' (as '%s') to shared storage.", username, actual_filename, safe_filename
    )
    return {"status": "success", "message": f"File '{safe_filename}' uploaded successfully."}
