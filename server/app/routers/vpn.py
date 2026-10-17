"""VPN profiles: what administrators import and set, and the gateways of this node."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from .. import routing, vpn
from ..models import VpnEndpointsImport, VpnImportRequest, VpnProfileUpdate, VpnProfileView
from ..security import EncryptedRoute, get_decrypted_request_body, verify_admin
from .uploads import reassemble_file

admin_router = APIRouter(
    prefix="/api/admin/vpn",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)


async def _view(record: dict[str, Any]) -> VpnProfileView:
    return vpn.view(record, await vpn.tunnels(record["id"]))


@admin_router.get("", response_model=list[VpnProfileView])
async def list_profiles() -> list[VpnProfileView]:
    """List the profiles, each with its gateways on this node."""
    views = [await _view(record) for record in vpn.state.vpn_profiles.values()]
    return sorted(views, key=lambda v: v.name.lower())


@admin_router.post("/import", response_model=list[VpnProfileView], status_code=201)
async def import_profiles(
    body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_admin)
) -> list[VpnProfileView]:
    """Make profiles of an uploaded config file or archive (see `vpn.import_profiles`)."""
    try:
        req = VpnImportRequest(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    path = await reassemble_file(user["username"], req.upload_id, req.total_chunks)
    try:
        if os.path.getsize(path) > vpn.MAX_BUNDLE_BYTES:
            raise HTTPException(status_code=413, detail="The upload is larger than 64 MiB.")
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        if os.path.exists(path):
            os.remove(path)
    try:
        records = await asyncio.to_thread(
            vpn.import_profiles, req.name, req.filename, data, req.username, req.password, req.users, req.groups, req.kind
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await vpn.save()
    return [await _view(record) for record in records]


@admin_router.put("/{profile_id}", response_model=VpnProfileView)
async def update_profile(profile_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> VpnProfileView:
    """Change a profile's name, published endpoints, cap, access, labels, or credentials."""
    record = vpn.state.vpn_profiles.get(profile_id)
    if not record:
        raise HTTPException(status_code=404, detail="VPN profile not found.")
    try:
        changes = VpnProfileUpdate(**body).model_dump(exclude_unset=True)
        vpn.update_profile(record, changes)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    except (ValueError, vpn.Unusable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await vpn.save()
    return await _view(record)


@admin_router.post("/{profile_id}/endpoints", response_model=VpnProfileView)
async def add_endpoints(
    profile_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_admin)
) -> VpnProfileView:
    """Add the endpoints of an uploaded config file or archive to a profile."""
    record = vpn.state.vpn_profiles.get(profile_id)
    if not record:
        raise HTTPException(status_code=404, detail="VPN profile not found.")
    try:
        req = VpnEndpointsImport(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    path = await reassemble_file(user["username"], req.upload_id, req.total_chunks)
    try:
        if os.path.getsize(path) > vpn.MAX_BUNDLE_BYTES:
            raise HTTPException(status_code=413, detail="The upload is larger than 64 MiB.")
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        if os.path.exists(path):
            os.remove(path)
    try:
        await asyncio.to_thread(vpn.add_endpoints, record, req.filename, data)
    except (ValueError, vpn.Unusable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await vpn.save()
    return await _view(record)


@admin_router.delete("/{profile_id}", status_code=204)
async def delete_profile(profile_id: str) -> Response:
    """Delete a profile; its gateways stay up until the sessions using them end."""
    if profile_id not in vpn.state.vpn_profiles:
        raise HTTPException(status_code=404, detail="VPN profile not found.")
    del vpn.state.vpn_profiles[profile_id]
    await vpn.save()
    return Response(status_code=204)
