"""Browser policies: the policies an administrator keeps, and the catalogs their editor is drawn from."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from .. import browser_policies, routing
from ..models import BrowserPolicy
from ..security import EncryptedRoute, get_decrypted_request_body, verify_admin
from ..state import state

admin_router = APIRouter(
    prefix="/api/admin/browser-policies",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)


def _policy(policy_id: str, body: dict[str, Any]) -> BrowserPolicy:
    """Read a policy from a request.

    Raises:
        HTTPException: 422 for a body that is no policy.
    """
    content = {
        key: value for key, value in (body or {}).items() if key in BrowserPolicy.model_fields and key != "id"
    }
    try:
        return BrowserPolicy(id=policy_id, **content)
    except (ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _listed(policy: BrowserPolicy) -> dict[str, Any]:
    return {**policy.model_dump(), "used_by": browser_policies.used_by(policy.id)}


@admin_router.get("")
async def list_policies() -> dict[str, Any]:
    """Return the policies, each with the applications that name it, and where their files are mounted.

    Returns:
        `policies`, sorted by name, each with `used_by`; `paths`, per engine the container
        paths a policy's file is mounted at and the browsers that read each.
    """
    policies = sorted(state.browser_policies.values(), key=lambda policy: policy.name.lower())
    return {"policies": [_listed(policy) for policy in policies], "paths": browser_policies.paths()}


@admin_router.get("/catalog/{engine}")
async def get_catalog(engine: str, lang: str = "") -> dict[str, Any]:
    """Return the catalog of an engine's policies, `chromium` or `firefox`.

    With `lang`, a locale of the web UI, the titles and descriptions are the engine's own
    translations where the server was packaged with them (`browser_policies.localized`).

    Raises:
        HTTPException: 404 for another engine.
    """
    try:
        return browser_policies.localized(engine, lang[:16])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No such browser engine.") from exc


@admin_router.post("", status_code=201)
async def create_policy(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Keep a new policy.

    Raises:
        HTTPException: 400 for a value its policy does not take, or a name another policy has.
    """
    try:
        return _listed(await browser_policies.put(_policy(browser_policies.new_id(), body)))
    except browser_policies.PolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.put("/{policy_id}")
async def update_policy(policy_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Replace a policy; sessions started from then on run under it.

    Raises:
        HTTPException: 404 when there is no such policy; 400 as for a new one.
    """
    if policy_id not in state.browser_policies:
        raise HTTPException(status_code=404, detail="Browser policy not found.")
    try:
        return _listed(await browser_policies.put(_policy(policy_id, body)))
    except browser_policies.PolicyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@admin_router.delete("/{policy_id}", status_code=204)
async def delete_policy(policy_id: str) -> Response:
    """Remove a policy no application names.

    Raises:
        HTTPException: 404 when there is no such policy; 409 while an application names it.
    """
    try:
        await browser_policies.delete(policy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Browser policy not found.") from exc
    except browser_policies.PolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)
