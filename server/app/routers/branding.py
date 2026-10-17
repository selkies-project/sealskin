"""The brand as the pages read it and as an administrator sets it.

The pictures are public: the logo is the favicon of the sign-in page, which
nobody is signed in to, and both are addressed by hash so a browser keeps
them. The record itself is read by signed-in users (`GET /api/branding`),
and written, with the pictures, by administrators under `/api/admin/branding`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from .. import branding, routing
from ..models import BrandImageUpload, Branding, BrandingView
from ..security import EncryptedRoute, get_decrypted_request_body, verify_admin, verify_token

router = APIRouter(prefix="/api/branding")
# The record is read by signed-in users of every shell, so it answers the encrypted lane too; the pictures stay plain.
user_router = APIRouter(prefix="/api/branding", route_class=EncryptedRoute)
admin_router = APIRouter(
    prefix="/api/admin/branding",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)


@user_router.get("", response_model=BrandingView)
async def get_branding(user: dict[str, Any] = Depends(verify_token)) -> BrandingView:
    """Return the brand with its pictures."""
    return branding.view()


@router.get("/logo", include_in_schema=False)
async def logo() -> Response:
    """Serve the uploaded logo."""
    return branding.image_response("logo")


@router.get("/wallpaper", include_in_schema=False)
async def wallpaper() -> Response:
    """Serve the uploaded wallpaper."""
    return branding.image_response("wallpaper")


@admin_router.put("", response_model=BrandingView)
async def write_branding(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> BrandingView:
    """Replace the brand's record; the pictures stay."""
    branding.save(Branding.model_validate(body))
    return branding.view()


@admin_router.delete("", response_model=BrandingView)
async def reset_branding() -> BrandingView:
    """Remove the record and the pictures, back to the defaults."""
    branding.reset()
    return branding.view()


@admin_router.post("/{kind}", response_model=BrandingView)
async def upload_picture(kind: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> BrandingView:
    """Keep an uploaded logo or wallpaper."""
    branding.save_image(kind, BrandImageUpload.model_validate(body).data)
    return branding.view()


@admin_router.delete("/{kind}", response_model=BrandingView)
async def delete_picture(kind: str) -> BrandingView:
    """Remove the logo or the wallpaper."""
    branding.remove_image(kind)
    return branding.view()
