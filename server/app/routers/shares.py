"""Public file shares: management for owners and anonymous download pages."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import ValidationError

from .. import config_store
from ..models import PublicShareInfo, PublicShareMetadata, ShareFileRequest
from ..security import (
    EncryptedRoute,
    get_decrypted_request_body,
    hash_share_password,
    verify_public_sharing_enabled,
    verify_share_password,
)
from ..settings import settings
from ..state import state
from .files import get_validated_path

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/files",
    dependencies=[Depends(verify_public_sharing_enabled)],
    route_class=EncryptedRoute,
)
public_router = APIRouter()

DOWNLOAD_TOKEN_TTL = 60


def _share_info(share_id: str, meta: PublicShareMetadata) -> PublicShareInfo:
    """Build the owner-facing view of a share."""
    return PublicShareInfo(
        share_id=share_id,
        url=f"/public/{share_id}",
        has_password=bool(meta.password_hash),
        original_filename=meta.original_filename,
        size_bytes=meta.size_bytes,
        created_at=meta.created_at,
        expiry_timestamp=meta.expiry_timestamp,
    )


@router.post("/share", response_model=PublicShareInfo)
async def create_public_share(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_public_sharing_enabled),
) -> PublicShareInfo:
    """Copy a file into public storage and register a share for it."""
    try:
        req = ShareFileRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    username = user["username"]
    source = get_validated_path(username, req.home_dir, req.path)
    if not source.is_file():
        raise HTTPException(status_code=400, detail="Path does not point to a file.")

    share_id = str(uuid.uuid4())
    try:
        os.makedirs(settings.public_storage_path, exist_ok=True, mode=0o700)
        await asyncio.to_thread(shutil.copy, source, os.path.join(settings.public_storage_path, share_id))
        stat_info = source.stat()
        expiry = None
        if req.expiry_hours is not None and req.expiry_hours > 0:
            expiry = time.time() + req.expiry_hours * 3600
        metadata = PublicShareMetadata(
            owner_username=username,
            original_filename=source.name,
            created_at=time.time(),
            size_bytes=stat_info.st_size,
            password_hash=hash_share_password(req.password) if req.password else None,
            expiry_timestamp=expiry,
        )
        state.public_shares[share_id] = metadata
        await config_store.save_public_shares()
        return _share_info(share_id, metadata)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to create share for user '%s': %s", username, exc)
        raise HTTPException(status_code=500, detail="Failed to create share.") from exc


@router.get("/shares", response_model=list[PublicShareInfo])
async def list_public_shares(
    user: dict[str, Any] = Depends(verify_public_sharing_enabled),
) -> list[PublicShareInfo]:
    """List the caller's shares, newest first."""
    shares = [
        _share_info(sid, meta)
        for sid, meta in state.public_shares.items()
        if meta.owner_username == user["username"]
    ]
    return sorted(shares, key=lambda s: s.created_at, reverse=True)


@router.delete("/share/{share_id}", status_code=204)
async def delete_public_share(
    share_id: str, user: dict[str, Any] = Depends(verify_public_sharing_enabled)
) -> Response:
    """Delete one of the caller's shares."""
    metadata = state.public_shares.get(share_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Share not found or permission denied.")
    if metadata.owner_username != user["username"]:
        raise HTTPException(status_code=403, detail="Share not found or permission denied.")
    _remove_share_file(share_id)
    del state.public_shares[share_id]
    await config_store.save_public_shares()
    return Response(status_code=204)


def _remove_share_file(share_id: str) -> None:
    """Delete the public copy of a shared file, logging failures."""
    path = os.path.join(settings.public_storage_path, share_id)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            logger.error("Error deleting share file for %s: %s", share_id, exc)


async def cleanup_expired_shares() -> None:
    """Remove expired shares and stale download tokens."""
    now = time.time()
    expired_tokens = [t for t, d in state.download_tokens.items() if d.get("expires_at", 0) < now]
    for token in expired_tokens:
        state.download_tokens.pop(token, None)

    expired = [
        sid for sid, meta in state.public_shares.items()
        if meta.expiry_timestamp and meta.expiry_timestamp < now
    ]
    if not expired:
        return
    logger.info("Found %d expired share(s) to clean up.", len(expired))
    for share_id in expired:
        _remove_share_file(share_id)
        state.public_shares.pop(share_id, None)
    await config_store.save_public_shares()
    logger.info("Expired share cleanup complete.")


def _password_page(share_id: str, error: str = "") -> HTMLResponse | None:
    """Render the password prompt page, or ``None`` if the template is missing."""
    page_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "public_password.html")
    if not os.path.exists(page_path):
        return None
    with open(page_path, encoding="utf-8") as handle:
        html = handle.read().replace("{{SHARE_ID}}", share_id).replace("{{ERROR_MESSAGE}}", error)
    return HTMLResponse(content=html, status_code=401 if error else 200)


def _share_or_404(share_id: str) -> PublicShareMetadata:
    """Return share metadata or raise 404."""
    metadata = state.public_shares.get(share_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Share not found.")
    return metadata


def _file_response(share_id: str, metadata: PublicShareMetadata) -> FileResponse:
    """Stream the shared file with its original name."""
    path = os.path.join(settings.public_storage_path, share_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Shared file not found on disk.")
    return FileResponse(path=path, filename=metadata.original_filename, media_type="application/octet-stream")


@public_router.get("/public/download/{token}")
async def download_shared_file(token: str) -> FileResponse:
    """Download a password-protected share with a one-time token."""
    token_data = state.download_tokens.pop(token, None)
    if not token_data or token_data.get("expires_at", 0) < time.time():
        raise HTTPException(status_code=403, detail="Invalid or expired download token.")
    share_id = token_data["share_id"]
    return _file_response(share_id, _share_or_404(share_id))


@public_router.get("/public/{share_id}")
async def access_public_share_get(share_id: str) -> Response:
    """Serve a share, or the password prompt when it is protected."""
    metadata = _share_or_404(share_id)
    if metadata.expiry_timestamp and metadata.expiry_timestamp < time.time():
        return HTMLResponse(content="<h1>This link has expired.</h1>", status_code=410)
    if metadata.password_hash:
        return _password_page(share_id) or HTMLResponse(
            content="<h1>Password protected</h1>", status_code=500
        )
    return _file_response(share_id, metadata)


@public_router.post("/public/{share_id}")
async def access_public_share_post(share_id: str, password: str = Form(...)) -> Response:
    """Check a share password and redirect to a one-time download URL."""
    metadata = _share_or_404(share_id)
    if metadata.expiry_timestamp and metadata.expiry_timestamp < time.time():
        return HTMLResponse(content="<h1>This link has expired.</h1>", status_code=410)
    if not metadata.password_hash:
        raise HTTPException(status_code=400, detail="This share is not password protected.")

    if verify_share_password(password, metadata.password_hash):
        token = secrets.token_urlsafe(32)
        state.download_tokens[token] = {
            "share_id": share_id,
            "expires_at": time.time() + DOWNLOAD_TOKEN_TTL,
        }
        return RedirectResponse(url=f"/public/download/{token}", status_code=303)
    return _password_page(share_id, "Incorrect password. Please try again.") or HTMLResponse(
        content="<h1>Incorrect Password</h1>", status_code=401
    )
