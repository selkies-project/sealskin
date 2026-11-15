"""The brand as the pages read it and as an administrator sets it.

The pictures are public: the logo is the favicon of the sign-in page, which
nobody is signed in to, and both are addressed by hash so a browser keeps
them. A signed-in user reads the brand their request wears
(`GET /api/branding`: the variant its host name or their groups pick, else
the brand itself); administrators read every brand and write the record and
the pictures under `/api/admin/branding`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response

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
async def get_branding(request: Request, user: dict[str, Any] = Depends(verify_token)) -> BrandingView:
    """Return the brand the caller's request wears, with its pictures."""
    return branding.view(branding.choose(request.headers.get("host", ""), user.get("groups") or ()))


@router.get("/logo", include_in_schema=False)
async def logo(b: str = "") -> Response:
    """Serve an uploaded logo: the variant's that `b` names where it has one, else the brand's."""
    return branding.image_response("logo", b)


@router.get("/wallpaper", include_in_schema=False)
async def wallpaper(b: str = "") -> Response:
    """Serve an uploaded wallpaper: the variant's that `b` names where it has one, else the brand's."""
    return branding.image_response("wallpaper", b)


@admin_router.get("", response_model=list[BrandingView])
async def read_brands() -> list[BrandingView]:
    """Return the brand itself and then every variant, each with its pictures."""
    return branding.views()


@admin_router.put("", response_model=list[BrandingView])
async def write_branding(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> list[BrandingView]:
    """Replace the record, variants included; the pictures stay, short of those of variants it no longer has."""
    branding.save(Branding.model_validate(body))
    return branding.views()


@admin_router.delete("", response_model=list[BrandingView])
async def reset_branding() -> list[BrandingView]:
    """Remove the record and the pictures, back to the defaults."""
    branding.reset()
    return branding.views()


@admin_router.post("/{kind}", response_model=list[BrandingView])
async def upload_picture(kind: str, brand: str = "", body: dict[str, Any] = Depends(get_decrypted_request_body)) -> list[BrandingView]:
    """Keep an uploaded logo or wallpaper, for the variant `brand` names or the brand itself."""
    branding.save_image(kind, BrandImageUpload.model_validate(body).data, brand)
    return branding.views()


@admin_router.delete("/{kind}", response_model=list[BrandingView])
async def delete_picture(kind: str, brand: str = "") -> list[BrandingView]:
    """Remove a logo or a wallpaper, of the variant `brand` names or the brand itself."""
    branding.remove_image(kind, brand)
    return branding.views()
