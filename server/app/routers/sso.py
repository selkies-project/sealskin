"""Identity provider sign-in routes for the web app (see `app.sso`).

`router` is public: it starts and finishes the provider flows, serves this
server's SAML metadata, takes the provider's logout notices, and registers the
browser key a finished flow grants. A finished flow sends the browser to the
web app with the grant, or the code of what went wrong, in the URL fragment,
which reaches neither a server nor a Referer. `signed_in_router` ends the
caller's sign-in.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from .. import sso
from ..models import SignInConfig, SignInRegistration, SignInRegistrationRequest
from ..security import EncryptedRoute, verify_token

logger = logging.getLogger(__name__)
router = APIRouter()
signed_in_router = APIRouter(route_class=EncryptedRoute)


def _base(request: Request) -> str:
    """Return the base URL the browser reached this server on."""
    return str(request.base_url).rstrip("/")


def _to_app(fragment: str) -> RedirectResponse:
    """Send the browser to the web app with `fragment`."""
    return RedirectResponse(f"/ui/#{fragment}", status_code=303)


def _failed(exc: sso.SignInError) -> RedirectResponse:
    """Log a refused sign-in and send the browser to the web app with its code, ending the flow's cookie."""
    logger.warning("Refused an identity provider sign-in: %r", str(exc))
    response = _to_app(f"sso-error={exc.code}")
    response.delete_cookie(sso.COOKIE, path="/api/auth/")
    return response


async def _begin(request: Request, start: Callable[[str, str], Awaitable[str]]) -> Response:
    """Start a provider flow bound to this browser by a cookie and redirect to the provider."""
    binding = secrets.token_urlsafe(32)
    try:
        url = await start(_base(request), binding)
    except sso.SignInError as exc:
        return _failed(exc)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        sso.COOKIE,
        binding,
        max_age=sso.FLOW_SECONDS,
        path="/api/auth/",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/api/auth/config", response_model=SignInConfig)
async def sign_in_config() -> dict[str, bool]:
    """Return the identity provider sign-ins this server offers."""
    return sso.enabled()


@router.get("/api/auth/oidc/login", include_in_schema=False)
async def oidc_login(request: Request) -> Response:
    """Send the browser to the OpenID Connect provider."""
    return await _begin(request, sso.oidc_start)


@router.get("/api/auth/oidc/callback", include_in_schema=False)
async def oidc_callback(request: Request, state: str = "", code: str = "", error: str = "") -> Response:
    """Finish an OpenID Connect flow and hand the web app its grant."""
    try:
        if error:
            raise sso.SignInError("refused", f"the provider answered {error!r}")
        binding = request.cookies.get(sso.COOKIE, "")
        identity = await sso.oidc_finish(state, code, binding)
    except sso.SignInError as exc:
        return _failed(exc)
    return _to_app(f"sso={sso.issue_grant(identity, binding)}")


@router.post("/api/auth/oidc/backchannel-logout", include_in_schema=False)
async def oidc_backchannel_logout(logout_token: str = Form("")) -> Response:
    """End the sign-ins a back-channel logout token of the provider names."""
    try:
        await sso.oidc_backchannel_logout(logout_token)
    except sso.SignInError as exc:
        logger.warning("Refused a logout token: %r", str(exc))
        return Response(status_code=400, headers={"Cache-Control": "no-store"})
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


@router.get("/api/auth/oidc/frontchannel-logout", include_in_schema=False)
async def oidc_frontchannel_logout(iss: str = "", sid: str = "") -> Response:
    """End the sign-ins of the session a front-channel logout of the provider names."""
    try:
        await sso.oidc_frontchannel_logout(iss, sid)
    except sso.SignInError as exc:
        logger.warning("Could not act on a front-channel logout: %r", str(exc))
    return Response(status_code=200, media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/api/auth/saml/login", include_in_schema=False)
async def saml_login(request: Request) -> Response:
    """Send the browser to the SAML provider."""
    return await _begin(request, sso.saml_start)


@router.post("/api/auth/saml/acs", include_in_schema=False)
async def saml_acs(SAMLResponse: str = Form(""), RelayState: str = Form("")) -> Response:  # noqa: N803
    """Check the provider's assertion, then finish in the browser's own request, which carries the cookie."""
    try:
        ticket = await sso.saml_acs(SAMLResponse, RelayState)
    except sso.SignInError as exc:
        return _failed(exc)
    return RedirectResponse(f"/api/auth/saml/done?ticket={ticket}", status_code=303)


@router.get("/api/auth/saml/done", include_in_schema=False)
async def saml_done(request: Request, ticket: str = "") -> Response:
    """Hand the web app the grant for an assertion `saml_acs` checked."""
    try:
        binding = request.cookies.get(sso.COOKIE, "")
        identity = sso.saml_done(ticket, binding)
    except sso.SignInError as exc:
        return _failed(exc)
    return _to_app(f"sso={sso.issue_grant(identity, binding)}")


@router.get("/api/auth/saml/metadata", include_in_schema=False)
async def saml_metadata(request: Request) -> Response:
    """Return the SAML metadata an administrator registers this server with."""
    return Response(sso.sp_metadata(_base(request)), media_type="application/samlmetadata+xml")


@router.api_route("/api/auth/saml/slo", methods=["GET", "POST"], include_in_schema=False)
async def saml_logout(request: Request) -> Response:
    """End the sign-ins a logout request of the provider names and answer it."""
    form = {k: str(v) for k, v in (await request.form()).items()} if request.method == "POST" else {}
    try:
        url = await sso.saml_logout(_base(request), request.url.query, form)
    except sso.SignInError as exc:
        logger.warning("Refused a logout request: %r", str(exc))
        raise HTTPException(status_code=400, detail=exc.code) from exc
    return RedirectResponse(url, status_code=303)


@router.post("/api/auth/register", response_model=SignInRegistration)
async def register(req: SignInRegistrationRequest, request: Request, response: Response) -> dict[str, Any]:
    """Register the web app's browser key for the sign-in its grant carries, in the browser that signed in."""
    response.delete_cookie(sso.COOKIE, path="/api/auth/")
    try:
        return await sso.register(req.grant, req.public_key, request.cookies.get(sso.COOKIE, ""))
    except sso.SignInError as exc:
        logger.warning("Refused a sign-in key: %r", str(exc))
        raise HTTPException(status_code=400, detail=exc.code) from exc


@signed_in_router.post("/api/auth/signout", status_code=204)
async def sign_out(user: dict[str, Any] = Depends(verify_token)) -> Response:
    """End the caller's identity provider sign-in; a key-file login has none."""
    if user.get("kid"):
        await sso.revoke(user["kid"])
    return Response(status_code=204)
