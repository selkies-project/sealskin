"""Web sign-ins: through an identity provider (OpenID Connect, SAML 2.0) or with the root token.

A sign-in is a random token the browser holds in the `SESSION_COOKIE`
cookie, which names a record here: the user, how they signed in, the groups
the provider named, and when it ends. `security.verify_token` asks
`current` for it on every request.

* A provider flow is bound to the browser that started it by a cookie, so a
  response the provider sent another browser is refused, and so is its grant:
  a link carrying someone else's grant signs nobody in. The SAML POST arrives
  cross-site, where a SameSite=Lax cookie is withheld, so the assertion is
  checked there and the binding one redirect later (`saml_done`).
* A sign-in lasts as long as the provider's: OpenID Connect ends at the ID
  token's expiry, or, with a refresh token, refreshes at most once every
  `CHECK_SECONDS` while in use and ends when the provider refuses, picking up
  group changes as it does; SAML ends at the assertion's
  `SessionNotOnOrAfter`. A logout the provider announces ends the sign-in at
  once: an OpenID Connect back-channel logout token or front-channel request
  naming its session, or a signed SAML logout request. Every sign-in ends
  after `sso_max_age_seconds`. `sso_force_login` makes the provider ask for
  credentials every time.
* The user name and groups are claims of the ID token, or of the provider's
  UserInfo endpoint where the ID token leaves them out (`_with_userinfo`); a
  SAML attribute is named by its `Name` or its `FriendlyName`, in
  `saml_username_attribute` and `saml_groups_attribute` where the two
  protocols name them differently.
* The first sign-in as a SealSkin user through each protocol binds it to the
  provider's subject there (`user_manager.ensure_user`), and another subject
  is refused as that user later. A key-file administrator's name is refused,
  since administrators come from `sso_admin_group` alone.
* The `root` administrator signs in with the root token (`check_root_token`),
  of which the shared store keeps only a hash. Its sign-in ends after
  `web_session_seconds` unused. A user a reverse proxy names has no sign-in
  here: the proxy's header is read on each request (`app.proxy_auth`).

Sign-ins are this node's own (`sso_keys_path`, written 0600): an OpenID
Connect one keeps its refresh token so a restarted server can still ask the
provider. Only the hash of a sign-in's token is kept.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import time
import zlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote_plus, urlencode
from xml.sax.saxutils import escape, quoteattr

import httpx
import jwt
from cryptography import x509
from cryptography.exceptions import InvalidSignature as CryptoInvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from lxml import etree
from signxml import SignatureConfiguration, XMLVerifier
from signxml.exceptions import SignXMLException

from . import audit, persistence, user_manager
from .settings import settings

logger = logging.getLogger(__name__)

CHECK_SECONDS = 60
FLOW_SECONDS = 600
GRANT_SECONDS = 120
CACHE_SECONDS = 3600
CLOCK_SKEW_SECONDS = 120
MAX_PENDING = 10000
COOKIE = "sealskin_sso"
#: Cookie holding a web sign-in; the `__Host-` prefix keeps a session's origin from setting it.
SESSION_COOKIE = "__Host-sealskin"
ROOT_TRIES = 5
BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
ASYMMETRIC_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}

SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
MD = "urn:oasis:names:tc:SAML:2.0:metadata"
DS = "http://www.w3.org/2000/09/xmldsig#"
REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
REDIRECT_SIGNATURES = {
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": hashes.SHA256,
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": hashes.SHA384,
    "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": hashes.SHA512,
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256": hashes.SHA256,
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha384": hashes.SHA384,
    "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha512": hashes.SHA512,
}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)

_KEYS: dict[str, dict[str, Any]] = {}
_ROOT_FAILURES: dict[str, list[float]] = {}
_FLOWS: dict[str, dict[str, Any]] = {}
_TICKETS: dict[str, dict[str, Any]] = {}
_GRANTS: dict[str, dict[str, Any]] = {}
_REPLAYS: dict[str, float] = {}
_CACHE: dict[str, tuple[float, Any]] = {}
_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}
_transport: httpx.AsyncBaseTransport | None = None


class SignInError(Exception):
    """A sign-in the server refuses, with the code the web app shows a message for.

    Codes: `notConfigured`, `failed`, `refused`, `expired`, `browser`,
    `invalidUsername`, `adminName`, `otherIdentity`, `noAccount`.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        """Keep the code for the client and the detail for the log."""
        super().__init__(detail or code)
        self.code = code


class _Ended(Exception):
    """The provider refused a refresh: the sign-in is over."""


def enabled() -> dict[str, bool]:
    """Return which sign-ins the settings configure."""
    return {
        "oidc": bool(settings.oidc_issuer and settings.oidc_client_id),
        "saml": bool(settings.saml_metadata_url),
        "proxy": bool(settings.proxy_auth_user_header),
        "root": True,
        "key": bool(settings.legacy_auth),
    }


def load() -> None:
    """Read this node's sign-ins, dropping the expired ones."""
    data = persistence.read_yaml(settings.sso_keys_path, {}) or {}
    _KEYS.clear()
    _KEYS.update(data.get("keys") or {})
    now = time.time()
    for kid in [k for k, reg in _KEYS.items() if reg.get("expires", 0) <= now]:
        del _KEYS[kid]
    if _KEYS:
        logger.info("Loaded %d web sign-in(s).", len(_KEYS))


async def _save() -> None:
    """Write this node's sign-ins."""
    await persistence.write_yaml(settings.sso_keys_path, {"keys": dict(_KEYS)})
    try:
        os.chmod(settings.sso_keys_path, 0o600)
    except OSError:
        pass


def session_id(token: str) -> str:
    """Return the id a sign-in is kept under: the hash of its token."""
    return hashlib.sha256(token.encode()).hexdigest()


async def open_session(identity: dict[str, Any]) -> str:
    """Start a sign-in for `identity` and return the token the browser keeps."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    for stale in [k for k, reg in _KEYS.items() if reg["expires"] <= now]:
        del _KEYS[stale]
    _KEYS[session_id(token)] = {**identity, "created": now, "checked": now}
    await _save()
    audit.record("sign_in", identity["username"], via=identity["via"], admin=identity["admin"] or None)
    logger.info(
        "'%s' signed in through %s%s.", identity["username"], identity["via"], " as an administrator" if identity["admin"] else ""
    )
    return token


def _root_hash(token: str) -> str:
    """Return the hash the store keeps of a root token."""
    return hashlib.sha256(f"sealskin-root {token}".encode()).hexdigest()


def ensure_root_token() -> None:
    """Make sure the cluster has a root token, generating and writing one out the first time.

    `SEALSKIN_ROOT_TOKEN` sets it; otherwise a generated one is written to
    `root_token_path` for the administrator to copy and delete. The store
    keeps the hash alone.
    """
    path = os.path.join(settings.cluster_path, "root.yml")
    known = persistence.read_yaml(path, None)
    given = settings.root_token.strip()
    if given:
        if not known or known.get("hash") != _root_hash(given):
            persistence.write_yaml_sync(path, {"hash": _root_hash(given)})
        return
    if known:
        return
    token = secrets.token_urlsafe(48)
    try:
        persistence.write_yaml_sync(path, {"hash": _root_hash(token)})
    except Exception as exc:  # noqa: BLE001 - another node wrote it first
        logger.info("The root token was set elsewhere: %s", exc)
        return
    try:
        fd = os.open(settings.root_token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
        logger.warning(
            "Generated the root token; sign in to the web app as 'root' with the content of %s, then delete that file.",
            settings.root_token_path,
        )
    except OSError as exc:
        logger.error("Could not write the root token to %s: %s", settings.root_token_path, exc)


async def root_sign_in(token: str, address: str = "") -> str:
    """Sign `root` in with the root token and return the sign-in's token.

    Args:
        token: The token offered.
        address: The client's address, which wrong tokens are counted by.

    Raises:
        SignInError: `refused` for a wrong token, `expired` after too many
            wrong ones from `address` within a minute.
    """
    now = time.time()
    for source in list(_ROOT_FAILURES):
        _ROOT_FAILURES[source] = [stamp for stamp in _ROOT_FAILURES[source] if now - stamp < 60]
        if not _ROOT_FAILURES[source]:
            del _ROOT_FAILURES[source]
    # Counted per address, so wrong tokens from elsewhere never keep the administrator out.
    if len(_ROOT_FAILURES.get(address, [])) >= ROOT_TRIES:
        raise SignInError("expired", "too many wrong root tokens; try again in a minute")
    known = persistence.read_yaml(os.path.join(settings.cluster_path, "root.yml"), None) or {}
    expected = str(known.get("hash") or "")
    if not expected or not secrets.compare_digest(_root_hash(token.strip()), expected):
        if len(_ROOT_FAILURES) < MAX_PENDING:
            _ROOT_FAILURES.setdefault(address, []).append(now)
        audit.record("sign_in_refused", user_manager.ROOT, via="root", address=address)
        raise SignInError("refused", "the root token is wrong")
    return await open_session(
        {
            "username": user_manager.ROOT,
            "admin": True,
            "groups": [],
            "subject": "root",
            "via": "root",
            "expires": now + settings.web_session_seconds,
        }
    )


def _prune() -> None:
    """Forget flows, tickets, grants, and replay entries past their lifetime.

    Anyone can start a flow, so each table keeps its newest `MAX_PENDING` entries at most.
    """
    now = time.time()
    for table, lifetime in ((_FLOWS, FLOW_SECONDS), (_TICKETS, FLOW_SECONDS), (_GRANTS, GRANT_SECONDS)):
        for key in [k for k, v in table.items() if now - v["created"] > lifetime]:
            del table[key]
        for key in list(table)[: max(0, len(table) - MAX_PENDING)]:
            del table[key]
    for key in [k for k, until in _REPLAYS.items() if until < now]:
        del _REPLAYS[key]


def _client() -> httpx.AsyncClient:
    """Return an HTTP client for the provider."""
    return httpx.AsyncClient(timeout=10, transport=_transport, follow_redirects=True)


async def _cached(key: str, fetch: Callable[[], Awaitable[Any]], fresh: bool = False) -> Any:
    """Return `fetch()`'s value, fetched at most once per `CACHE_SECONDS` unless `fresh`."""
    hit = _CACHE.get(key)
    if hit and not fresh and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = await fetch()
    _CACHE[key] = (time.time(), value)
    return value


def _b64url(data: bytes) -> str:
    """Return unpadded base64url text."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _groups(value: Any) -> set[str]:
    """Return a claim or attribute's groups, a leading `/` of a group path dropped."""
    if isinstance(value, str):
        value = [value]
    return {str(g).strip().lstrip("/") for g in value or [] if str(g).strip()}


def _admit(username: Any, subject: str) -> None:
    """Refuse a name SealSkin cannot use, a key-file administrator's, or one another account holds."""
    if not isinstance(username, str) or not username:
        raise SignInError("invalidUsername", "the provider named no user")
    try:
        user_manager.validate_name(username)
    except ValueError as exc:
        raise SignInError("invalidUsername", f"{username!r} is not a SealSkin user name") from exc
    user = user_manager.get_user(username)
    if user and user.get("is_admin"):
        raise SignInError("adminName", f"{username!r} is a key-file administrator or root")
    bound = ((user or {}).get("settings") or {}).get("auth") or {}
    if bound.get(subject.split(" ", 1)[0], subject) != subject:
        raise SignInError("otherIdentity", f"{username!r} is bound to another account of the provider")


def _identity(username: Any, groups: Any, subject: str, via: str, expires: float, **extra: Any) -> dict[str, Any]:
    """Map what the provider asserted onto a SealSkin user, or refuse it.

    Args:
        username: The value of the username claim or attribute.
        groups: The value of the groups claim or attribute.
        subject: The provider's stable name for the account.
        via: `oidc` or `saml`.
        expires: When the sign-in ends at the latest.
        **extra: Provider state the registration keeps (refresh token, SAML
            session).

    Returns:
        The identity a grant carries.

    Raises:
        SignInError: See `_admit`.
    """
    _admit(username, subject)
    admin_group = settings.sso_admin_group.strip().lstrip("/")
    cap = time.time() + settings.sso_max_age_seconds
    return {
        "username": username,
        "admin": bool(admin_group) and admin_group in _groups(groups),
        "groups": sorted(_groups(groups)),
        "subject": subject,
        "via": via,
        "expires": min(expires, cap),
        **extra,
    }


def _take_flow(state_token: str, via: str) -> dict[str, Any]:
    """Pop the flow `state_token` names, or refuse an unknown or stale one."""
    _prune()
    flow = _FLOWS.pop(state_token, None)
    if not flow or flow["via"] != via:
        raise SignInError("expired", "no sign-in is waiting for this response")
    return flow


def _check_binding(expected: str, cookie: str) -> None:
    """Refuse a flow finishing in a browser other than the one that started it."""
    if not cookie or not secrets.compare_digest(expected.encode(), cookie.encode()):
        raise SignInError("browser", "the sign-in finished in another browser")


def issue_grant(identity: dict[str, Any], binding: str) -> str:
    """Return a one-time grant the web app of the browser holding `binding` exchanges for a sign-in."""
    grant = secrets.token_urlsafe(32)
    _GRANTS[grant] = {"identity": identity, "binding": binding, "created": time.time()}
    _prune()
    return grant


async def register(grant: str, binding: str) -> dict[str, Any]:
    """Start the sign-in `grant` carries, in the browser that went through the provider.

    Args:
        grant: A grant from `issue_grant`, usable once.
        binding: The flow cookie of the browser asking.

    Returns:
        `token` for the `SESSION_COOKIE`, `username`, `via`, and `expires`.

    Raises:
        SignInError: For a spent or unknown grant, one another browser was
            issued, or a user that changed since the provider answered.
    """
    _prune()
    entry = _GRANTS.pop(grant, None)
    if not entry:
        raise SignInError("expired", "the grant was spent or never issued")
    _check_binding(entry["binding"], binding)
    identity = entry["identity"]
    username = identity["username"]
    _admit(username, identity["subject"])
    try:
        user_manager.ensure_user(username, identity["via"], identity["subject"], identity.get("groups") or ())
    except user_manager.NoAccount as exc:
        raise SignInError("noAccount", str(exc)) from exc
    except ValueError as exc:
        raise SignInError("refused", str(exc)) from exc
    token = await open_session(identity)
    return {"token": token, "username": username, "via": identity["via"], "expires": identity["expires"]}


async def revoke(kid: str) -> None:
    """End the sign-in kept under `kid`."""
    _REFRESH_LOCKS.pop(kid, None)
    if _KEYS.pop(kid, None) is not None:
        await _save()


async def end_sign_ins_of(username: str) -> None:
    """End every sign-in of a user on this node, as when the account is removed."""
    ended = [kid for kid, reg in _KEYS.items() if reg["username"] == username]
    for kid in ended:
        _KEYS.pop(kid, None)
    if ended:
        await _save()


async def current(kid: str) -> dict[str, Any] | None:
    """Return the sign-in kept under `kid` while it lasts.

    An OpenID Connect sign-in is refreshed when the last check is older than
    `CHECK_SECONDS`; a refusal ends it, an unreachable provider does not. A
    root or proxy sign-in is pushed out by `web_session_seconds` each time it
    is used.
    """
    reg = _KEYS.get(kid)
    if not reg or reg["expires"] <= time.time():
        await revoke(kid)
        return None
    if reg["via"] == "root":
        if time.time() - reg["checked"] >= CHECK_SECONDS:
            reg["checked"] = time.time()
            reg["expires"] = reg["checked"] + settings.web_session_seconds
            await _save()
        return reg
    if reg["via"] != "oidc" or not reg.get("refresh_token") or time.time() - reg["checked"] < CHECK_SECONDS:
        return reg
    lock = _REFRESH_LOCKS.setdefault(kid, asyncio.Lock())
    async with lock:
        reg = _KEYS.get(kid)
        if not reg or time.time() - reg["checked"] < CHECK_SECONDS:
            return reg
        try:
            await _refresh(reg)
        except _Ended as exc:
            logger.info("The identity provider ended the sign-in of '%s': %r", reg["username"], str(exc))
            await revoke(kid)
            return None
        except (httpx.HTTPError, SignInError, ValueError) as exc:
            logger.warning("Could not check the sign-in of '%s' with the identity provider: %r", reg["username"], str(exc))
            reg["checked"] = time.time()
        await _save()
        return reg


# --- OpenID Connect ------------------------------------------------------------


async def _discovery(fresh: bool = False) -> dict[str, Any]:
    """Return the provider's configuration, whose issuer must be the one configured; `fresh` asks it again."""

    async def fetch() -> dict[str, Any]:
        async with _client() as client:
            response = await client.get(f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration")
        response.raise_for_status()
        doc = response.json()
        if str(doc.get("issuer", "")).rstrip("/") != settings.oidc_issuer.rstrip("/"):
            raise SignInError("failed", f"the provider names itself {doc.get('issuer')!r}")
        return doc

    if not enabled()["oidc"]:
        raise SignInError("notConfigured")
    try:
        return await _cached(f"oidc {settings.oidc_issuer}", fetch, fresh)
    except (httpx.HTTPError, ValueError) as exc:
        raise SignInError("failed", f"the provider's configuration could not be read: {exc}") from exc


async def _signing_key(token: str, doc: dict[str, Any]) -> tuple[Any, str]:
    """Return the provider key and algorithm an ID token names, fetching the key set again once if unknown."""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise SignInError("failed", "the ID token is malformed") from exc
    alg = header.get("alg")
    if alg not in ASYMMETRIC_ALGORITHMS:
        raise SignInError("failed", f"the ID token is signed with {alg!r}")

    async def fetch() -> dict[str, Any]:
        async with _client() as client:
            response = await client.get(doc["jwks_uri"])
        response.raise_for_status()
        return response.json()

    for fresh in (False, True):
        try:
            keys = (await _cached(f"jwks {doc['jwks_uri']}", fetch, fresh=fresh)).get("keys", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise SignInError("failed", f"the provider's keys could not be read: {exc}") from exc
        for jwk in keys:
            if jwk.get("use", "sig") == "sig" and header.get("kid") in (None, jwk.get("kid")):
                try:
                    return jwt.PyJWK(jwk, algorithm=alg).key, alg
                except jwt.PyJWTError:
                    continue
    raise SignInError("failed", f"no key of the provider matches the ID token's kid {header.get('kid')!r}")


async def _verify_id_token(token: Any, doc: dict[str, Any], nonce: str | None = None) -> dict[str, Any]:
    """Return an ID token's claims once its signature, issuer, audience, times, and nonce check out."""
    if not isinstance(token, str):
        raise SignInError("failed", "the provider returned no ID token")
    key, alg = await _signing_key(token, doc)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=settings.oidc_client_id,
            issuer=doc["issuer"],
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SignInError("failed", f"the ID token does not verify: {exc}") from exc
    if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
        raise SignInError("failed", "the ID token answers another sign-in")
    audience = claims["aud"]
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != settings.oidc_client_id:
        raise SignInError("failed", "the ID token was issued to another client")
    return claims


async def _token_request(doc: dict[str, Any], form: dict[str, str]) -> dict[str, Any]:
    """Post to the token endpoint with this client's credentials.

    Raises:
        _Ended: When the provider refuses the grant (400 or 401).
        httpx.HTTPError: When it cannot be reached or fails otherwise.
    """
    auth = None
    form = dict(form)
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if settings.oidc_client_secret and "client_secret_basic" in methods:
        auth = (settings.oidc_client_id, settings.oidc_client_secret)
    else:
        form["client_id"] = settings.oidc_client_id
        if settings.oidc_client_secret:
            form["client_secret"] = settings.oidc_client_secret
    async with _client() as client:
        response = await client.post(doc["token_endpoint"], data=form, auth=auth, headers={"Accept": "application/json"})
    if response.status_code in (400, 401):
        raise _Ended(response.text[:200])
    response.raise_for_status()
    return response.json()


async def _with_userinfo(claims: dict[str, Any], tokens: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Return `claims` with the user name and groups the ID token left to the UserInfo endpoint.

    A provider may keep an ID token to the claims that identify the sign-in
    and serve the rest there, as Authelia does unless a claims policy says
    otherwise. UserInfo is believed only for the account the ID token names.

    Raises:
        httpx.HTTPError: When the endpoint cannot be reached or refuses.
        ValueError: When its answer is not that account's claims.
    """
    wanted = [settings.sso_username_claim or "preferred_username", settings.sso_groups_claim]
    missing = [name for name in wanted if name and name not in claims]
    endpoint, access_token = doc.get("userinfo_endpoint"), tokens.get("access_token")
    if not missing or not endpoint or not access_token:
        return claims
    async with _client() as client:
        response = await client.get(endpoint, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
    response.raise_for_status()
    info = response.json()
    if not isinstance(info, dict) or info.get("sub") != claims["sub"]:
        raise ValueError("the UserInfo answer names another account than the ID token")
    return claims | {name: info[name] for name in missing if name in info}


def _oidc_expiry(tokens: dict[str, Any], claims: dict[str, Any]) -> float:
    """Return when an OpenID Connect sign-in ends by the provider's own account of it."""
    if tokens.get("refresh_expires_in"):
        return time.time() + float(tokens["refresh_expires_in"])
    if tokens.get("refresh_token"):
        return time.time() + settings.sso_max_age_seconds
    return float(claims["exp"])


async def oidc_start(base_url: str, binding: str) -> str:
    """Begin an authorization code flow with PKCE and return the provider's URL to send the browser to."""
    doc = await _discovery()
    state_token, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    redirect_uri = f"{base_url}/api/auth/oidc/callback"
    _FLOWS[state_token] = {
        "via": "oidc",
        "binding": binding,
        "created": time.time(),
        "nonce": nonce,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    _prune()
    query = urlencode(
        ({"prompt": "login"} if settings.sso_force_login else {})
        | {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": settings.oidc_scopes,
            "state": state_token,
            "nonce": nonce,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
        }
    )
    endpoint = doc["authorization_endpoint"]
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"


async def oidc_finish(state_token: str, code: str, binding: str) -> dict[str, Any]:
    """Exchange the code the provider returned and verify its ID token.

    Returns:
        The identity for `issue_grant`.
    """
    flow = _take_flow(state_token, "oidc")
    _check_binding(flow["binding"], binding)
    doc = await _discovery()
    try:
        tokens = await _token_request(
            doc,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": flow["redirect_uri"],
                "code_verifier": flow["verifier"],
            },
        )
    except (_Ended, httpx.HTTPError, ValueError) as exc:
        raise SignInError("failed", f"the code exchange failed: {exc}") from exc
    claims = await _verify_id_token(tokens.get("id_token"), doc, nonce=flow["nonce"])
    try:
        claims = await _with_userinfo(claims, tokens, doc)
    except (httpx.HTTPError, ValueError) as exc:
        raise SignInError("failed", f"the UserInfo request failed: {exc}") from exc
    return _identity(
        claims.get(settings.sso_username_claim or "preferred_username"),
        claims.get(settings.sso_groups_claim) if settings.sso_groups_claim else [],
        f"oidc {doc['issuer']} {claims['sub']}",
        "oidc",
        _oidc_expiry(tokens, claims),
        refresh_token=tokens.get("refresh_token"),
        oidc_sub=claims["sub"],
        oidc_sid=claims.get("sid"),
    )


async def _refresh(reg: dict[str, Any]) -> None:
    """Refresh an OpenID Connect sign-in in place, taking the groups the provider names now."""
    doc = await _discovery()
    tokens = await _token_request(doc, {"grant_type": "refresh_token", "refresh_token": reg["refresh_token"]})
    reg["refresh_token"] = tokens.get("refresh_token") or reg["refresh_token"]
    reg["checked"] = time.time()
    if tokens.get("id_token"):
        claims = await _verify_id_token(tokens["id_token"], doc)
        if claims["sub"] != reg.get("oidc_sub"):
            raise _Ended("the refreshed ID token names another account")
        if settings.sso_groups_claim:
            claims = await _with_userinfo(claims, tokens, doc)
            admin_group = settings.sso_admin_group.strip().lstrip("/")
            reg["groups"] = sorted(_groups(claims.get(settings.sso_groups_claim)))
            reg["admin"] = bool(admin_group) and admin_group in reg["groups"]
    if tokens.get("refresh_expires_in"):
        reg["expires"] = min(time.time() + float(tokens["refresh_expires_in"]), reg["created"] + settings.sso_max_age_seconds)


async def _end_oidc(issuer: str, sid: str | None, sub: str | None) -> int:
    """End the OpenID Connect sign-ins of session `sid`, or of every session of `sub`.

    A sign-in whose ID token named no session is matched by `sub` alone.
    """
    ended = [
        kid
        for kid, reg in _KEYS.items()
        if reg["via"] == "oidc"
        and reg["subject"].startswith(f"oidc {issuer} ")
        and (not sub or reg.get("oidc_sub") == sub)
        and (reg.get("oidc_sid") == sid if sid and reg.get("oidc_sid") else bool(sub))
    ]
    for kid in ended:
        del _KEYS[kid]
    if ended:
        await _save()
        logger.info("The identity provider ended %d sign-in(s).", len(ended))
    return len(ended)


async def oidc_backchannel_logout(logout_token: str) -> int:
    """End the sign-ins a back-channel logout token names, once it verifies as the provider's.

    Returns:
        How many sign-ins ended.

    Raises:
        SignInError: For a token that does not verify, is stale or replayed, or is not a logout token.
    """
    doc = await _discovery()
    key, alg = await _signing_key(logout_token, doc)
    try:
        claims = jwt.decode(
            logout_token,
            key,
            algorithms=[alg],
            audience=settings.oidc_client_id,
            issuer=doc["issuer"],
            leeway=60,
            options={"require": ["iat", "iss", "aud", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise SignInError("failed", f"the logout token does not verify: {exc}") from exc
    if "nonce" in claims or BACKCHANNEL_LOGOUT_EVENT not in (claims.get("events") or {}):
        raise SignInError("failed", "not a logout token")
    if not claims.get("sid") and not claims.get("sub"):
        raise SignInError("failed", "the logout token names no session or subject")
    if claims["iat"] < time.time() - FLOW_SECONDS:
        raise SignInError("expired", "the logout token is stale")
    _prune()
    replay = f"jti {claims['jti']}"
    if replay in _REPLAYS:
        raise SignInError("failed", "the logout token was already used")
    _REPLAYS[replay] = time.time() + FLOW_SECONDS
    return await _end_oidc(doc["issuer"], claims.get("sid"), claims.get("sub"))


async def oidc_frontchannel_logout(issuer: str, sid: str) -> int:
    """End the sign-ins of the session a front-channel logout request names, if it names one of this provider."""
    doc = await _discovery()
    if not sid or issuer.rstrip("/") != doc["issuer"].rstrip("/"):
        return 0
    return await _end_oidc(doc["issuer"], sid, None)


# --- SAML ----------------------------------------------------------------------


def _parse_time(value: str | None) -> float | None:
    """Return an `xs:dateTime` as a Unix time, or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise SignInError("failed", f"the time {value!r} is malformed") from exc


def _instant() -> str:
    """Return the current time as an `xs:dateTime` in UTC."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _idp(fresh: bool = False) -> dict[str, Any]:
    """Return the provider's entity ID, endpoints, and signing certificates from its metadata; `fresh` reads it again."""

    async def fetch() -> dict[str, Any]:
        async with _client() as client:
            response = await client.get(settings.saml_metadata_url)
        response.raise_for_status()
        root = etree.fromstring(response.content, parser=_PARSER)
        entity = next(
            (e for e in root.iter(f"{{{MD}}}EntityDescriptor") if e.find(f"{{{MD}}}IDPSSODescriptor") is not None),
            None,
        )
        if entity is None:
            raise SignInError("failed", "the metadata describes no identity provider")
        descriptor = entity.find(f"{{{MD}}}IDPSSODescriptor")
        sso_urls = {s.get("Binding"): s.get("Location") for s in descriptor.findall(f"{{{MD}}}SingleSignOnService")}
        slo = {
            s.get("Binding"): s.get("ResponseLocation") or s.get("Location")
            for s in descriptor.findall(f"{{{MD}}}SingleLogoutService")
        }
        certs = []
        for key_descriptor in descriptor.findall(f"{{{MD}}}KeyDescriptor"):
            if key_descriptor.get("use", "signing") != "signing":
                continue
            for cert in key_descriptor.iter(f"{{{DS}}}X509Certificate"):
                der = base64.b64decode("".join((cert.text or "").split()))
                certs.append(x509.load_der_x509_certificate(der))
        if REDIRECT not in sso_urls:
            raise SignInError("failed", "the provider offers no HTTP-Redirect sign-on endpoint")
        if not certs:
            raise SignInError("failed", "the metadata carries no signing certificate")
        return {"entity_id": entity.get("entityID"), "sso_url": sso_urls[REDIRECT], "slo": slo, "certs": certs}

    if not enabled()["saml"]:
        raise SignInError("notConfigured")
    try:
        return await _cached(f"saml {settings.saml_metadata_url}", fetch, fresh)
    except (httpx.HTTPError, etree.XMLSyntaxError, ValueError) as exc:
        raise SignInError("failed", f"the provider's metadata could not be read: {exc}") from exc


def sp_urls(base_url: str) -> dict[str, str]:
    """Return this server's SAML entity ID and endpoints for `base_url`."""
    return {
        "entity_id": f"{base_url}/api/auth/saml/metadata",
        "acs": f"{base_url}/api/auth/saml/acs",
        "slo": f"{base_url}/api/auth/saml/slo",
    }


def sp_metadata(base_url: str) -> str:
    """Return the metadata an administrator registers this server with at the provider."""
    urls = sp_urls(base_url)
    return (
        f'<md:EntityDescriptor xmlns:md="{MD}" entityID={quoteattr(urls["entity_id"])}>'
        f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        f'protocolSupportEnumeration="{SAMLP}">'
        f'<md:SingleLogoutService Binding="{REDIRECT}" Location={quoteattr(urls["slo"])}/>'
        f'<md:SingleLogoutService Binding="{POST}" Location={quoteattr(urls["slo"])}/>'
        "<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified</md:NameIDFormat>"
        f'<md:AssertionConsumerService Binding="{POST}" Location={quoteattr(urls["acs"])} index="0" isDefault="true"/>'
        "</md:SPSSODescriptor></md:EntityDescriptor>"
    )


def _redirect_encode(xml: str) -> str:
    """Deflate and base64 a message for the HTTP-Redirect binding."""
    compressor = zlib.compressobj(wbits=-15)
    return base64.b64encode(compressor.compress(xml.encode()) + compressor.flush()).decode()


def _redirect_decode(value: str) -> bytes:
    """Undo `_redirect_encode`."""
    try:
        return zlib.decompress(base64.b64decode(value), wbits=-15)
    except (ValueError, zlib.error) as exc:
        raise SignInError("failed", "the message is not deflated base64") from exc


async def saml_start(base_url: str, binding: str) -> str:
    """Build an AuthnRequest and return the provider's URL carrying it."""
    idp = await _idp()
    urls = sp_urls(base_url)
    request_id = "_" + secrets.token_hex(20)
    force = 'ForceAuthn="true" ' if settings.sso_force_login else ""
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{SAMLP}" xmlns:saml="{SAML}" ID="{request_id}" Version="2.0" '
        f'IssueInstant="{_instant()}" Destination={quoteattr(idp["sso_url"])} {force}'
        f'ProtocolBinding="{POST}" AssertionConsumerServiceURL={quoteattr(urls["acs"])}>'
        f"<saml:Issuer>{escape(urls['entity_id'])}</saml:Issuer>"
        '<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified" AllowCreate="true"/>'
        "</samlp:AuthnRequest>"
    )
    state_token = secrets.token_urlsafe(32)
    _FLOWS[state_token] = {"via": "saml", "binding": binding, "created": time.time(), "request_id": request_id, **urls}
    _prune()
    query = urlencode({"SAMLRequest": _redirect_encode(xml), "RelayState": state_token})
    return f"{idp['sso_url']}{'&' if '?' in idp['sso_url'] else '?'}{query}"


def _verify_xml(document: bytes, location: str, certs: list[x509.Certificate]) -> Any:
    """Return the element a signature at `location` signs, verified against one of `certs`."""
    last: Exception | None = None
    for cert in certs:
        try:
            return XMLVerifier().verify(
                document, x509_cert=cert, expect_config=SignatureConfiguration(location=location)
            ).signed_xml
        except SignXMLException as exc:
            last = exc
    raise SignInError("failed", f"the signature does not verify: {last}")


def _signed_assertion(document: bytes, idp: dict[str, Any]) -> Any:
    """Return the one assertion of a Response, taken from what the provider signed.

    The Response is signed as a whole or its assertion is; either way only the
    element the signature covers is read, so data wrapped around it is never trusted.
    """
    try:
        root = etree.fromstring(document, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise SignInError("failed", "the response is not XML") from exc
    if root.tag != f"{{{SAMLP}}}Response":
        raise SignInError("failed", f"expected a Response, got {root.tag!r}")
    if root.find(f"{{{SAML}}}EncryptedAssertion") is not None:
        raise SignInError("failed", "encrypted assertions are not supported")
    if len(root.findall(f"{{{SAML}}}Assertion")) != 1:
        raise SignInError("failed", "the response carries other than one assertion")
    if root.find(f"{{{DS}}}Signature") is not None:
        signed = _verify_xml(document, "./", idp["certs"])
    else:
        signed = _verify_xml(document, f"./{{{SAML}}}Assertion/", idp["certs"])
    if signed.tag == f"{{{SAMLP}}}Response":
        code = signed.find(f"{{{SAMLP}}}Status/{{{SAMLP}}}StatusCode")
        if code is None or code.get("Value") != SUCCESS:
            raise SignInError("refused", f"the provider answered {None if code is None else code.get('Value')!r}")
        assertions = signed.findall(f"{{{SAML}}}Assertion")
        if len(assertions) != 1:
            raise SignInError("failed", f"the response carries {len(assertions)} assertions")
        return signed, assertions[0]
    if signed.tag == f"{{{SAML}}}Assertion":
        return root, signed
    raise SignInError("failed", f"the signature covers a {signed.tag!r}")


def _read_assertion(document: bytes, flow: dict[str, Any], idp: dict[str, Any]) -> dict[str, Any]:
    """Validate a Response against the request it answers and return the identity it asserts."""
    response, assertion = _signed_assertion(document, idp)
    code = response.find(f"{{{SAMLP}}}Status/{{{SAMLP}}}StatusCode")
    if code is None or code.get("Value") != SUCCESS:
        raise SignInError("refused", f"the provider answered {None if code is None else code.get('Value')!r}")
    if response.get("Destination") not in (None, flow["acs"]):
        raise SignInError("failed", f"the response is addressed to {response.get('Destination')!r}")
    if response.get("InResponseTo") not in (None, flow["request_id"]):
        raise SignInError("failed", "the response answers another request")
    if assertion.findtext(f"{{{SAML}}}Issuer") != idp["entity_id"]:
        raise SignInError("failed", f"the assertion was issued by {assertion.findtext(f'{{{SAML}}}Issuer')!r}")
    now = time.time()
    conditions = assertion.find(f"{{{SAML}}}Conditions")
    if conditions is None:
        raise SignInError("failed", "the assertion states no conditions")
    not_before, not_after = _parse_time(conditions.get("NotBefore")), _parse_time(conditions.get("NotOnOrAfter"))
    if (not_before and not_before > now + CLOCK_SKEW_SECONDS) or not not_after or not_after <= now - CLOCK_SKEW_SECONDS:
        raise SignInError("failed", "the assertion is outside its validity")
    audiences = {a.text for a in conditions.iter(f"{{{SAML}}}Audience")}
    if flow["entity_id"] not in audiences:
        raise SignInError("failed", f"the assertion is for {sorted(audiences)}")
    subject = assertion.find(f"{{{SAML}}}Subject")
    name_id = subject.findtext(f"{{{SAML}}}NameID") if subject is not None else None
    confirmed = False
    for confirmation in subject.findall(f"{{{SAML}}}SubjectConfirmation") if subject is not None else []:
        data = confirmation.find(f"{{{SAML}}}SubjectConfirmationData")
        if confirmation.get("Method") != BEARER or data is None:
            continue
        until = _parse_time(data.get("NotOnOrAfter"))
        if (
            data.get("Recipient") == flow["acs"]
            and data.get("InResponseTo") == flow["request_id"]
            and until
            and until > now - CLOCK_SKEW_SECONDS
        ):
            confirmed = True
    if not name_id or not confirmed:
        raise SignInError("failed", "the assertion confirms no bearer for this request")
    assertion_id = assertion.get("ID") or ""
    if not assertion_id or assertion_id in _REPLAYS:
        raise SignInError("failed", "the assertion was already used")
    _REPLAYS[assertion_id] = not_after + CLOCK_SKEW_SECONDS
    attributes: dict[str, list[str]] = {}
    friendly_names: dict[str, list[str]] = {}
    for attribute in assertion.iter(f"{{{SAML}}}Attribute"):
        values = [v.text or "" for v in attribute.findall(f"{{{SAML}}}AttributeValue")]
        attributes.setdefault(attribute.get("Name", ""), []).extend(values)
        friendly = attribute.get("FriendlyName")
        if friendly:
            friendly_names.setdefault(friendly, values)
    # An attribute answers to its FriendlyName too, where no attribute has that Name.
    attributes = friendly_names | attributes
    statement = assertion.find(f"{{{SAML}}}AuthnStatement")
    session_until = _parse_time(statement.get("SessionNotOnOrAfter")) if statement is not None else None
    claim = settings.saml_username_attribute or settings.sso_username_claim
    groups_claim = settings.saml_groups_attribute or settings.sso_groups_claim
    username = (attributes.get(claim) or [None])[0] if claim else (attributes.get("username") or [name_id])[0]
    return _identity(
        username,
        attributes.get(groups_claim, []) if groups_claim else [],
        f"saml {idp['entity_id']} {name_id}",
        "saml",
        session_until or now + settings.sso_max_age_seconds,
        name_id=name_id,
        session_index=statement.get("SessionIndex") if statement is not None else None,
    )


async def saml_acs(saml_response: str, relay_state: str) -> str:
    """Validate the provider's POST and return a ticket `saml_done` redeems in the browser's own request."""
    flow = _take_flow(relay_state, "saml")
    try:
        document = base64.b64decode(saml_response, validate=True)
    except ValueError as exc:
        raise SignInError("failed", "the response is not base64") from exc
    identity = _read_assertion(document, flow, await _idp())
    ticket = secrets.token_urlsafe(32)
    _TICKETS[ticket] = {"identity": identity, "binding": flow["binding"], "created": time.time()}
    _prune()
    return ticket


def saml_done(ticket: str, binding: str) -> dict[str, Any]:
    """Redeem a ticket from `saml_acs` in the browser that started the flow."""
    _prune()
    entry = _TICKETS.pop(ticket, None)
    if not entry:
        raise SignInError("expired", "the ticket was spent or never issued")
    _check_binding(entry["binding"], binding)
    return entry["identity"]


def _redirect_signature_valid(raw_query: str, certs: list[x509.Certificate]) -> bool:
    """Check an HTTP-Redirect binding signature over the query parameters as the provider encoded them."""
    parts = dict(p.split("=", 1) for p in raw_query.split("&") if "=" in p)
    algorithm = REDIRECT_SIGNATURES.get(unquote_plus(parts.get("SigAlg", "")))
    if not algorithm or "Signature" not in parts or "SAMLRequest" not in parts:
        return False
    signed = "&".join(f"{k}={parts[k]}" for k in ("SAMLRequest", "RelayState", "SigAlg") if k in parts).encode()
    try:
        signature = base64.b64decode(unquote_plus(parts["Signature"]), validate=True)
    except ValueError:
        return False
    for cert in certs:
        key = cert.public_key()
        try:
            if isinstance(key, rsa.RSAPublicKey):
                key.verify(signature, signed, padding.PKCS1v15(), algorithm())
            elif isinstance(key, ec.EllipticCurvePublicKey):
                key.verify(signature, signed, ec.ECDSA(algorithm()))
            else:
                continue
            return True
        except CryptoInvalidSignature:
            continue
    return False


async def saml_logout(base_url: str, raw_query: str, form: dict[str, str]) -> str:
    """End the sign-ins a signed LogoutRequest from the provider names.

    Args:
        base_url: This server's base URL.
        raw_query: The request's query string as sent (HTTP-Redirect binding).
        form: The posted form (HTTP-POST binding), or empty.

    Returns:
        The provider URL carrying the LogoutResponse.

    Raises:
        SignInError: For an unsigned, forged, or misaddressed request.
    """
    idp = await _idp()
    try:
        if form.get("SAMLRequest"):
            document = base64.b64decode(form["SAMLRequest"], validate=True)
            request = _verify_xml(document, "./", idp["certs"])
            relay = form.get("RelayState")
        else:
            query = dict(p.split("=", 1) for p in raw_query.split("&") if "=" in p)
            if not _redirect_signature_valid(raw_query, idp["certs"]):
                raise SignInError("failed", "the logout request is not signed by the provider")
            request = etree.fromstring(_redirect_decode(unquote_plus(query["SAMLRequest"])), parser=_PARSER)
            relay = unquote_plus(query["RelayState"]) if "RelayState" in query else None
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise SignInError("failed", f"the logout request is malformed: {exc}") from exc
    if request.tag != f"{{{SAMLP}}}LogoutRequest" or request.findtext(f"{{{SAML}}}Issuer") != idp["entity_id"]:
        raise SignInError("failed", "not a logout request from the provider")
    if request.get("Destination") not in (None, sp_urls(base_url)["slo"]):
        raise SignInError("failed", f"the logout request is addressed to {request.get('Destination')!r}")
    until = _parse_time(request.get("NotOnOrAfter"))
    if until and until <= time.time() - CLOCK_SKEW_SECONDS:
        raise SignInError("expired", "the logout request expired")
    name_id = request.findtext(f"{{{SAML}}}NameID")
    sessions = {s.text for s in request.findall(f"{{{SAMLP}}}SessionIndex")}
    ended = [
        kid
        for kid, reg in _KEYS.items()
        if reg["via"] == "saml"
        and reg.get("name_id") == name_id
        and reg["subject"].startswith(f"saml {idp['entity_id']} ")
        and (not sessions or reg.get("session_index") in sessions)
    ]
    for kid in ended:
        del _KEYS[kid]
    if ended:
        await _save()
        logger.info("The identity provider ended %d sign-in(s) of %r.", len(ended), name_id)
    target = idp["slo"].get(REDIRECT)
    if not target:
        raise SignInError("failed", "the provider offers no HTTP-Redirect logout endpoint")
    reply = (
        f'<samlp:LogoutResponse xmlns:samlp="{SAMLP}" xmlns:saml="{SAML}" ID="_{secrets.token_hex(20)}" '
        f'Version="2.0" IssueInstant="{_instant()}" Destination={quoteattr(target)} '
        f"InResponseTo={quoteattr(request.get('ID', ''))}>"
        f"<saml:Issuer>{escape(sp_urls(base_url)['entity_id'])}</saml:Issuer>"
        f'<samlp:Status><samlp:StatusCode Value="{SUCCESS}"/></samlp:Status></samlp:LogoutResponse>'
    )
    params = {"SAMLResponse": _redirect_encode(reply)}
    if relay:
        params["RelayState"] = relay
    return f"{target}{'&' if '?' in target else '?'}{urlencode(params)}"
