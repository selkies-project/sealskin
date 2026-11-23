"""Collaboration profiles: the room features an administrator allows users and groups."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from .. import collaboration_profiles, routing
from ..models import CollaborationProfile
from ..security import EncryptedRoute, get_decrypted_request_body, verify_admin
from ..state import state

admin_router = APIRouter(
    prefix="/api/admin/collaboration-profiles",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)


def _profile(profile_id: str, body: dict[str, Any]) -> CollaborationProfile:
    """Read a profile from a request.

    Raises:
        HTTPException: 422 for a body that is no profile.
    """
    content = {
        key: value
        for key, value in (body or {}).items()
        if key in CollaborationProfile.model_fields and key != "id"
    }
    try:
        return CollaborationProfile(id=profile_id, **content)
    except (ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _listed(profile: CollaborationProfile) -> dict[str, Any]:
    return {**profile.model_dump(), "used_by": collaboration_profiles.used_by(profile.id)}


@admin_router.get("")
async def list_profiles() -> dict[str, Any]:
    """Return the profiles, sorted by name, each with the users and groups that name it (`used_by`)."""
    profiles = sorted(state.collaboration_profiles.values(), key=lambda profile: profile.name.lower())
    return {"profiles": [_listed(profile) for profile in profiles]}


@admin_router.post("", status_code=201)
async def create_profile(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Keep a new profile.

    Raises:
        HTTPException: 400 for a name another profile has.
    """
    try:
        return _listed(await collaboration_profiles.put(_profile(collaboration_profiles.new_id(), body)))
    except collaboration_profiles.ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.put("/{profile_id}")
async def update_profile(profile_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Replace a profile; rooms started from then on run under it.

    Raises:
        HTTPException: 404 when there is no such profile; 400 as for a new one.
    """
    if profile_id not in state.collaboration_profiles:
        raise HTTPException(status_code=404, detail="Collaboration profile not found.")
    try:
        return _listed(await collaboration_profiles.put(_profile(profile_id, body)))
    except collaboration_profiles.ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.delete("/{profile_id}", status_code=204)
async def delete_profile(profile_id: str) -> Response:
    """Remove a profile no user or group names.

    Raises:
        HTTPException: 404 when there is no such profile; 409 while a user or group names it.
    """
    try:
        await collaboration_profiles.delete(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Collaboration profile not found.") from exc
    except collaboration_profiles.ProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)
