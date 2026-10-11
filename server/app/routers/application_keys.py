"""Application keys as their user and an administrator manage them (see `app.application_keys`).

`router` is the user's own: list, make, and revoke keys, and wake them after
a web sign-in. It answers a web sign-in alone, the cookie's or the proxy's,
so a key, or a key-file client, cannot make keys: what leaks cannot spread,
and the web always wins. `admin_router` lists and revokes any user's keys,
from any sign-in of an administrator's.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import application_keys, cluster, routing, user_manager
from ..models import (
    ApplicationKey,
    ApplicationKeyCreated,
    ApplicationKeyCreateRequest,
    ApplicationKeyList,
)
from ..security import EncryptedRoute, verify_admin, verify_token
from ..settings import settings

router = APIRouter(prefix="/api/keys")
admin_router = APIRouter(
    prefix="/api/admin/users",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)

WEB_SIGN_INS = ("root", "oidc", "saml", "proxy")


async def web_user(user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """The caller, who has to be signed in on the web: keys are managed from the web app alone."""
    if user.get("via") not in WEB_SIGN_INS:
        raise HTTPException(status_code=403, detail="Application keys are managed from the web app.")
    return user


def _listing(username: str) -> dict[str, Any]:
    """Return a user's keys with the policy over them."""
    return {"keys": application_keys.list_for(username), "policy": application_keys.policy(username)}


@router.get("", response_model=ApplicationKeyList)
async def list_keys(user: dict[str, Any] = Depends(web_user)) -> dict[str, Any]:
    """Return the caller's keys and what the settings let them do with keys."""
    return await asyncio.to_thread(_listing, user["username"])


@router.post("", response_model=ApplicationKeyCreated, status_code=201)
async def make_key(req: ApplicationKeyCreateRequest, user: dict[str, Any] = Depends(web_user)) -> dict[str, Any]:
    """Make a key for the caller and return it with the server's address, this once; `403 disabled` where the settings deny keys, `400 days` over the longest lifetime."""
    try:
        key, record = await asyncio.to_thread(application_keys.create, user["username"], req.name, req.days)
    except application_keys.KeyRefused as exc:
        raise HTTPException(status_code=403 if exc.code == "disabled" else 400, detail=exc.code) from exc
    # The address browsers reach this node on, so the key alone sets an app up.
    return {"key": application_keys.with_address(key, cluster.public_url()), "record": record}


@router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: str, user: dict[str, Any] = Depends(web_user)) -> Response:
    """Take one of the caller's keys back."""
    if not await asyncio.to_thread(application_keys.revoke, key_id, user["username"]):
        raise HTTPException(status_code=404, detail="No such key.")
    return Response(status_code=204)


@router.post("/wake", response_model=ApplicationKeyList)
async def wake_keys(request: Request, user: dict[str, Any] = Depends(web_user)) -> dict[str, Any]:
    """Count the caller's web sign-in as happening now, which wakes keys the dormancy rules put to sleep.

    A browser that still holds a sign-in opens the web app without signing
    in again, so the companion app's "sign in on the web" lands here.
    """
    until = user.get("expires") or time.time() + settings.web_session_seconds
    await asyncio.to_thread(user_manager.note_web_sign_in, user["username"], float(until), True)
    return await asyncio.to_thread(_listing, user["username"])


@admin_router.get("/{username}/keys", response_model=list[ApplicationKey])
async def list_user_keys(username: str) -> list[dict[str, Any]]:
    """Return a user's keys."""
    if not user_manager.get_user(username):
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    return await asyncio.to_thread(application_keys.list_for, username)


@admin_router.delete("/{username}/keys/{key_id}", status_code=204)
async def revoke_user_key(username: str, key_id: str, admin: dict[str, Any] = Depends(verify_admin)) -> Response:
    """Take one of a user's keys back."""
    if not await asyncio.to_thread(application_keys.revoke, key_id, username, admin["username"]):
        raise HTTPException(status_code=404, detail="No such key.")
    return Response(status_code=204)


@admin_router.delete("/{username}/keys", status_code=204)
async def revoke_user_keys(username: str) -> Response:
    """Take every key of a user back."""
    if not user_manager.get_user(username):
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    await asyncio.to_thread(application_keys.revoke_all, username)
    return Response(status_code=204)
