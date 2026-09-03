"""Home directory management for the calling user."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from .. import user_manager
from ..models import HomeDirectoryCreate, HomeDirectoryList
from ..security import EncryptedRoute, get_decrypted_request_body, verify_persistent_storage_enabled

router = APIRouter(
    prefix="/api/homedirs",
    dependencies=[Depends(verify_persistent_storage_enabled)],
    route_class=EncryptedRoute,
)


@router.get("", response_model=HomeDirectoryList)
async def list_my_home_dirs(
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, list[str]]:
    """List the calling user's home directories."""
    return {"home_dirs": user_manager.get_home_dirs(user["username"])}


@router.post("", status_code=201)
async def create_my_home_dir(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, str]:
    """Create a home directory for the calling user."""
    try:
        req = HomeDirectoryCreate(**decrypted_body)
        user_manager.create_home_dir(user["username"], req.home_name)
        return {"status": "success", "home_name": req.home_name}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc


@router.delete("/{home_name}", status_code=204)
async def delete_my_home_dir(
    home_name: str, user: dict[str, Any] = Depends(verify_persistent_storage_enabled)
) -> Response:
    """Delete one of the calling user's home directories."""
    try:
        user_manager.delete_home_dir(user["username"], home_name)
        return Response(status_code=204)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
