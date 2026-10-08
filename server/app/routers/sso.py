"""Web sign-in routes (see `app.sso`).

`router` is public: it starts and finishes the provider flows, serves this
server's SAML metadata, takes the provider's logout notices, answers the
check of a reverse proxy that signs users in (`app.proxy_auth`), and turns the
grant of a finished flow, or the root token, into a sign-in the browser
keeps in a cookie. A finished flow sends the browser to the web app with the
grant, or the code of what went wrong, in the URL fragment, which reaches
neither a server nor a Referer. Signing out is here too, outside the
encrypted lane, whose answers carry no cookies.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .. import cluster, proxy_auth, sso
from ..models import RootSignInRequest, SignInConfig, SignInRegistration, SignInRegistrationRequest
from ..security import same_origin
from ..settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _base(request: Request) -> str:
    """Return the base URL the browser reached this server on; `public_url` when set."""
    return settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")


def _start_session(response: Response, started: dict[str, Any]) -> dict[str, Any]:
    """Hand the browser the cookie of a started sign-in."""
    response.set_cookie(sso.SESSION_COOKIE, started["token"], path="/", httponly=True, secure=True, samesite="lax")
    return started


def _web_only(request: Request) -> None:
    """Refuse a sign-in that did not come from the web app's own page over this node's proxy."""
    if not cluster.via_proxy(request) or not same_origin(request):
        raise HTTPException(status_code=403, detail="Sign in from the web app, over HTTPS.")


def _to_app(fragment: str) -> RedirectResponse:
    """Send the browser to the web app with `fragment`."""
    return RedirectResponse(f"/#{fragment}", status_code=303)


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
async def sign_in_config() -> dict[str, Any]:
    """Return the sign-ins this server offers, and the provider it sends a signed-out browser to at once."""
    return sso.enabled()


@router.get(proxy_auth.CHECK_PATH, include_in_schema=False)
async def proxy_check(request: Request) -> Response:
    """Say what a request carried when it arrived, for the check of the reverse proxy in front."""
    return JSONResponse(proxy_auth.answer(request), headers={"Cache-Control": "no-store"})


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
    """Start the sign-in a finished provider flow granted, in the browser that went through it."""
    _web_only(request)
    response.delete_cookie(sso.COOKIE, path="/api/auth/")
    try:
        return _start_session(response, await sso.register(req.grant, request.cookies.get(sso.COOKIE, "")))
    except sso.SignInError as exc:
        logger.warning("Refused a sign-in: %r", str(exc))
        raise HTTPException(status_code=400, detail=exc.code) from exc


@router.post("/api/auth/root", response_model=SignInRegistration)
async def root_sign_in(req: RootSignInRequest, request: Request, response: Response) -> dict[str, Any]:
    """Sign the root administrator in with the root token; refused where `root_sign_in` is off."""
    _web_only(request)
    if not settings.root_sign_in:
        raise HTTPException(status_code=403, detail="disabled")
    try:
        token = await sso.root_sign_in(req.token, cluster.client_address(request))
    except sso.SignInError as exc:
        logger.warning("Refused a root sign-in from %s: %r", cluster.client_address(request) or "an unknown address", str(exc))
        raise HTTPException(status_code=429 if exc.code == "expired" else 403, detail=exc.code) from exc
    started = {"token": token, "username": "root", "via": "root", "expires": time.time() + settings.web_session_seconds}
    return _start_session(response, started)


@router.post("/api/auth/signout", status_code=204)
async def sign_out(request: Request) -> Response:
    """End the web sign-in the caller's cookie names and take the cookie back.

    A key-file client has no sign-in to end, and a user a reverse proxy
    signed in has none here either; both are answered the same.
    """
    token = request.cookies.get(sso.SESSION_COOKIE)
    response = Response(status_code=204)
    if token and cluster.via_proxy(request):
        if not same_origin(request):
            raise HTTPException(status_code=403, detail="Requests from other origins are refused.")
        await sso.revoke(sso.session_id(token))
        response.delete_cookie(sso.SESSION_COOKIE, path="/", secure=True, httponly=True)
    return response
