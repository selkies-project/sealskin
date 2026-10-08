"""Authentication and end-to-end encryption.

* The E2EE handshake: the server signs a nonce with its RSA key so the client
  can verify it, then the client sends an RSA-OAEP wrapped AES-256-GCM key.
* `EncryptedRoute` encrypts every JSON response with the session key and
  `get_decrypted_request_body` decrypts request bodies.
* `verify_token` validates client-signed RS256 JWTs against the public
  key stored for the user, or the browser key their identity provider
  sign-in registered, which the token names by `kid`.
* Password hashing for public shares.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.datastructures import URL

from . import cluster, proxy_auth, sso, user_manager
from .models import EncryptedPayload
from .settings import settings
from .state import CryptoSession, state

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
JWT_LEEWAY_SECONDS = 60
HANDSHAKE_PATHS = ("/api/handshake/initiate", "/api/handshake/exchange")


def init_server_keys() -> None:
    """Load the server RSA key into `state` and publish the public PEM.

    Exits the process when the key file is missing, matching the behaviour
    administrators rely on to notice a broken volume mount.
    """
    try:
        with open(settings.server_private_key_path, "rb") as handle:
            private_key = serialization.load_pem_private_key(handle.read(), password=None)
    except FileNotFoundError as exc:
        logger.error("Key file not found: %s. Exiting.", exc.filename)
        sys.exit(1)

    state.server_private_key = private_key
    state.server_public_key_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    user_manager.set_server_public_key(state.server_public_key_pem)


def sign_nonce() -> tuple[str, str]:
    """Create a random nonce and sign it with the server key.

    Returns:
        `(nonce_b64, signature_b64)` using RSA-PSS with SHA-256.
    """
    nonce = os.urandom(32)
    signature = state.server_private_key.sign(
        nonce,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    return base64.b64encode(nonce).decode("utf-8"), base64.b64encode(signature).decode("utf-8")


def unwrap_session_key(encrypted_session_key_b64: str) -> bytes:
    """Decrypt a client-provided AES key wrapped with the server public key.

    Args:
        encrypted_session_key_b64: Base64 RSA-OAEP ciphertext.

    Returns:
        The raw AES key bytes.
    """
    return state.server_private_key.decrypt(
        base64.b64decode(encrypted_session_key_b64),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def register_crypto_session(aes_key: bytes) -> str:
    """Store a negotiated AES key and return its session id.

    Args:
        aes_key: Raw AES-256 key.

    Returns:
        A new random session id.
    """
    prune_crypto_sessions()
    session_id = secrets.token_hex(16)
    state.crypto_sessions[session_id] = CryptoSession(key=aes_key)
    return session_id


def prune_crypto_sessions() -> int:
    """Drop E2EE sessions idle longer than `crypto_session_ttl_seconds`.

    Returns:
        Number of sessions removed.
    """
    cutoff = time.time() - settings.crypto_session_ttl_seconds
    stale = [sid for sid, sess in state.crypto_sessions.items() if sess.last_used < cutoff]
    for sid in stale:
        state.crypto_sessions.pop(sid, None)
    if stale:
        logger.debug("Pruned %d idle crypto session(s).", len(stale))
    return len(stale)


def _touch_session(session_id: str) -> CryptoSession | None:
    """Return the crypto session for `session_id` and mark it as used."""
    session = state.crypto_sessions.get(session_id)
    if session:
        session.last_used = time.time()
    return session


def is_plain(request: Request) -> bool:
    """Whether a request belongs to the plain lane, whose bodies TLS alone protects.

    That is a request with no crypto session that came through this node's
    proxy, so over TLS: the web app's, a signed-in shell's, or another node's.
    A request to the API port itself always takes the encrypted lane.
    """
    return not request.headers.get("X-Session-ID") and cluster.via_proxy(request)


def same_origin(request: Request) -> bool:
    """Whether the browser vouches that a request comes from the web app's own pages.

    A cookie or a proxy's sign-in travels with any request the browser makes
    to this origin, so a session's page on a sibling origin must not ride it.
    """
    site = request.headers.get("sec-fetch-site")
    if site:
        return site in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin:
        return origin.split("://", 1)[-1] == request.headers.get("host", "") or origin == settings.public_url.rstrip("/")
    return request.method in ("GET", "HEAD")


async def get_decrypted_request_body(request: Request) -> dict[str, Any]:
    """FastAPI dependency returning the decrypted JSON body of a request.

    Args:
        request: Incoming request carrying `X-Session-ID` and an
            `EncryptedPayload` body.

    Returns:
        The decrypted JSON document.

    A request of the plain lane (see `is_plain`) carries the document itself.

    Raises:
        HTTPException: 400 when the session is unknown or decryption fails.
    """
    session_id = request.headers.get("X-Session-ID", "")
    if is_plain(request):
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="The request body is not JSON.") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="The request body is not a JSON object.")
        return body
    session = _touch_session(session_id) if session_id else None
    if not session:
        raise HTTPException(status_code=400, detail="Invalid or missing session ID")
    aesgcm = AESGCM(session.key)
    try:
        payload = EncryptedPayload(**(await request.json()))
        decrypted = aesgcm.decrypt(
            base64.b64decode(payload.iv), base64.b64decode(payload.ciphertext), None
        )
        return json.loads(decrypted)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to decrypt request for session %s...: %s", session_id[:8], exc)
        raise HTTPException(status_code=400, detail="Failed to decrypt request") from exc


IDEMPOTENCY_TTL_SECONDS = 600
_IDEMPOTENCY_RESULTS: dict[str, tuple[float, int, bytes, str]] = {}
_IDEMPOTENCY_INFLIGHT: dict[str, asyncio.Future[tuple[int, bytes, str]]] = {}


def _prune_idempotency_cache() -> None:
    """Drop cached responses older than `IDEMPOTENCY_TTL_SECONDS`."""
    cutoff = time.time() - IDEMPOTENCY_TTL_SECONDS
    for key in [k for k, (stamp, _, _, _) in _IDEMPOTENCY_RESULTS.items() if stamp < cutoff]:
        _IDEMPOTENCY_RESULTS.pop(key, None)


def _encrypt_json_response(session: CryptoSession, body: bytes, status_code: int) -> Response:
    """Encrypt a plaintext JSON body for the given crypto session."""
    iv = os.urandom(12)
    ciphertext = AESGCM(session.key).encrypt(iv, body, None)
    envelope = EncryptedPayload(
        iv=base64.b64encode(iv).decode("utf-8"),
        ciphertext=base64.b64encode(ciphertext).decode("utf-8"),
    )
    return JSONResponse(content=envelope.model_dump(), status_code=status_code)


class EncryptedRoute(APIRoute):
    """Route class that encrypts JSON responses with the E2EE session key.

    Non-GET requests carrying an `X-Idempotency-Key` header are executed at
    most once per crypto session: a retried request (for example after the
    client's network blipped while a container was being created) receives
    the stored result instead of running the handler again. While the first
    attempt is still running, the retry waits for it.

    A request of the plain lane (see `is_plain`) is answered as it is, and
    its idempotency key counts per credential. A request naming a crypto
    session this server does not hold is refused before the handler runs.
    """

    def get_route_handler(self) -> Callable:
        """Wrap the original handler so JSON responses are encrypted."""
        original_handler = super().get_route_handler()

        async def run_and_capture(request: Request) -> tuple[int, bytes, str]:
            try:
                response = await original_handler(request)
            except cluster.Forwarded as forwarded:
                # Another node's answer, kept like this node's own for a retried request.
                response = forwarded.response
            content_type = response.headers.get("content-type", "")
            body = response.body if hasattr(response, "body") else b""
            return response.status_code, body, content_type

        async def custom_handler(request: Request) -> Response:
            session_id = request.headers.get("X-Session-ID", "")
            session = _touch_session(session_id) if session_id else None
            plain = is_plain(request)
            if request.url.path not in HANDSHAKE_PATHS and not session and not plain:
                logger.warning(
                    "Security: Request to %s has invalid/missing session key.", request.url.path
                )
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Secure session required. Encryption key missing or invalid."},
                )

            idem_key = request.headers.get("X-Idempotency-Key", "").strip()
            principal = session_id
            if plain:
                credential = request.headers.get("cookie", "") + request.headers.get("authorization", "")
                principal = "plain:" + hashlib.sha256(credential.encode()).hexdigest()
            cache_key = f"{principal}:{request.method}:{request.url.path}:{idem_key}"
            use_idempotency = bool((session or plain) and idem_key and request.method != "GET")

            if use_idempotency:
                _prune_idempotency_cache()
                cached = _IDEMPOTENCY_RESULTS.get(cache_key)
                if cached:
                    logger.info("Idempotent replay for %s (key %s)", request.url.path, idem_key[:8])
                    _, status_code, body, content_type = cached
                elif cache_key in _IDEMPOTENCY_INFLIGHT:
                    logger.info(
                        "Idempotent wait for in-flight %s (key %s)", request.url.path, idem_key[:8]
                    )
                    status_code, body, content_type = await asyncio.shield(
                        _IDEMPOTENCY_INFLIGHT[cache_key]
                    )
                else:
                    future: asyncio.Future[tuple[int, bytes, str]] = (
                        asyncio.get_running_loop().create_future()
                    )
                    _IDEMPOTENCY_INFLIGHT[cache_key] = future
                    try:
                        result = await run_and_capture(request)
                    except BaseException as exc:
                        if not future.done():
                            future.set_exception(exc)
                            # Read here, so a failure no second request waited for is not logged as lost.
                            future.exception()
                        _IDEMPOTENCY_INFLIGHT.pop(cache_key, None)
                        raise
                    _IDEMPOTENCY_INFLIGHT.pop(cache_key, None)
                    if not future.done():
                        future.set_result(result)
                    status_code, body, content_type = result
                    if status_code < 500:
                        _IDEMPOTENCY_RESULTS[cache_key] = (time.time(), status_code, body, content_type)
            else:
                status_code, body, content_type = await run_and_capture(request)

            is_json = content_type.startswith("application/json")
            if plain or not (is_json and body):
                return Response(status_code=status_code, content=body, media_type=content_type or None)

            if session:
                try:
                    return _encrypt_json_response(session, body, status_code)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Encryption Error for %s: %s", request.url.path, exc)
                    return JSONResponse(
                        status_code=500, content={"detail": "Encryption failed server-side."}
                    )
            logger.error(
                "Security Block: Prevented unencrypted JSON response for %s.", request.url.path
            )
            return JSONResponse(
                status_code=400,
                content={"detail": "Secure session required. Encryption key missing or invalid."},
            )

        return custom_handler


def proxy_cert_not_after(cert_path: str) -> float | None:
    """Return the expiry time of the proxy TLS certificate as a Unix timestamp.

    Args:
        cert_path: Path to the PEM certificate Caddy serves.

    Returns:
        The `notAfter` timestamp, or `None` if the file is missing or
        cannot be parsed.
    """
    try:
        with open(cert_path, "rb") as handle:
            cert = x509.load_pem_x509_certificate(handle.read())
        return cert.not_valid_after_utc.timestamp()
    except (OSError, ValueError) as exc:
        logger.warning("Could not read proxy certificate %s: %s", cert_path, exc)
        return None


#: What a held user's sessions and requests would run under: nothing, until an administrator lets them in.
HELD_SETTINGS = {
    "persistent_storage": False,
    "public_sharing": False,
    "edit_templates": False,
    "gpu": False,
    "home_migration": False,
    "session_limit": 0,
    "pools": [],
}
#: The one route a held user may call, which tells the web app to show the holding page.
STATUS_PATH = "/api/admin/status"


def _signed_in(username: str, via: str, provider_groups: Any = (), admin: bool = False, **extra: Any) -> dict[str, Any]:
    """Build the record of an authenticated user, or refuse an unknown or inactive one.

    A held user (`user_manager.held`) is signed in with `held` set and the
    settings of `HELD_SETTINGS`; `verify_token` refuses every route but
    `STATUS_PATH` for one.
    """
    user = user_manager.get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token.")
    is_admin = bool(user.get("is_admin") or admin)
    effective = user_manager.get_effective_settings(username, provider_groups)
    is_admin = is_admin or bool(effective.get("admin"))
    held = False
    if is_admin:
        effective = dict(
            user_manager.DEFAULT_USER_SETTINGS,
            admin=True,
            groups=effective.get("groups") or [],
            proot_catalog=effective.get("proot_catalog"),
        )
    elif not effective.get("active", False):
        raise HTTPException(status_code=403, detail="User account is inactive.")
    elif user_manager.held(username, provider_groups):
        held = True
        effective = dict(effective, **HELD_SETTINGS)
    return dict(
        user,
        is_admin=is_admin,
        held=held,
        effective_settings=effective,
        group=effective.get("group", "none"),
        groups=effective.get("groups") or [],
        provider_groups=sorted(provider_groups or []),
        via=via,
        **extra,
    )


async def _peer_user(req: Request) -> dict[str, Any] | None:
    """Return the user a frontend's peer request acts as."""
    if not req.headers.get("authorization", "").startswith(cluster.SCHEME + " "):
        return None
    peer = await cluster.verify_request(req)
    act = peer["claims"].get("act")
    if not act or "frontend" not in (peer["node"].get("roles") or []):
        raise HTTPException(status_code=403, detail="This node may not act for users.")
    return _signed_in(
        str(act.get("username")),
        str(act.get("via") or "peer"),
        act.get("provider_groups") or (),
        bool(act.get("is_admin")),
        forwarded=True,
    )


async def _web_user(req: Request) -> dict[str, Any] | None:
    """Return the user of the web sign-in a request's cookie names, while it lasts."""
    token = req.cookies.get(sso.SESSION_COOKIE)
    if not token or not is_plain(req):
        return None
    signed_in = await sso.current(sso.session_id(token))
    if not signed_in:
        raise HTTPException(status_code=401, detail="This sign-in ended.")
    if not same_origin(req):
        raise HTTPException(status_code=403, detail="Requests from other origins are refused.")
    return _signed_in(
        signed_in["username"],
        signed_in["via"],
        signed_in.get("groups") or (),
        bool(signed_in.get("admin")),
        sid=sso.session_id(token),
        expires=signed_in.get("expires"),
    )


async def _proxy_user(req: Request) -> dict[str, Any] | None:
    """Return the user a reverse proxy signed in and names in its header, where the proxy is believed."""
    header = settings.proxy_auth_user_header.strip()
    if not header or not is_plain(req):
        return None
    username = req.headers.get(header, "").strip()
    if not username or not await proxy_auth.believed(req.headers.get("x-sealskin-remote", ""), req.headers.get("cookie", "")):
        return None
    if not same_origin(req):
        raise HTTPException(status_code=403, detail="Requests from other origins are refused.")
    # Authelia separates groups with commas, Authentik with bars.
    groups_header = settings.proxy_auth_groups_header.strip()
    listed = req.headers.get(groups_header, "").replace("|", ",") if groups_header else ""
    groups = [g.strip() for g in listed.split(",") if g.strip()]
    try:
        user_manager.ensure_user(username, "proxy", "", groups)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _signed_in(username, "proxy", groups, user_manager.in_admin_group(groups))


def _key_user(req: Request) -> dict[str, Any]:
    """Return the user of a key-file client's signed token."""
    auth_header = req.headers.get("Authorization", "")
    if not settings.legacy_auth or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header missing or invalid")
    token = auth_header.split(" ", 1)[1]
    try:
        unverified_claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token format.") from exc
    username = unverified_claims.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Token missing username claim.")
    user = user_manager.get_user(username)
    if not user or not user.get("public_key"):
        raise HTTPException(status_code=401, detail="Invalid token.")
    try:
        jwt.decode(
            token,
            user["public_key"],
            algorithms=[ALGORITHM],
            options={"require": ["exp"]},
            leeway=JWT_LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token signature or claims.") from exc
    return _signed_in(username, "key")


async def verify_token(req: Request) -> dict[str, Any]:
    """FastAPI dependency authenticating a request.

    In order: another node acting for a user it signed in, the web sign-in a
    cookie names, the user a trusted proxy names, and a key-file client's
    token (`sub` names the user, `exp` is required, and the signature is
    checked with the user's stored public key).

    Args:
        req: Incoming request.

    Returns:
        The user record with `is_admin`, `held`, `effective_settings`,
        `group`, `groups`, `provider_groups`, and `via` (`key`, `root`,
        `oidc`, `saml`, or `proxy`). `forwarded` marks a request another node
        sent on, which this node answers itself.

    Raises:
        HTTPException: 401 for an invalid credential, 403 for an inactive
            account, a held user anywhere but `STATUS_PATH`, or a request
            another origin made.
    """
    forwarded = await _peer_user(req)
    if forwarded or cluster.on_peer_listener(req):
        if not forwarded:
            raise HTTPException(status_code=401, detail="The peer listener takes other nodes' requests alone.")
        user = forwarded
    else:
        user = await _web_user(req) or await _proxy_user(req) or _key_user(req)
    if user.get("held") and req.url.path != STATUS_PATH:
        raise HTTPException(status_code=403, detail="This account waits for an administrator to place it in a group.")
    return user


async def verify_admin(user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """Dependency requiring an administrator."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


async def verify_template_editor(user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """Dependency requiring an administrator or a user allowed to edit app templates."""
    if user.get("is_admin") or user.get("effective_settings", {}).get("edit_templates", False):
        return user
    raise HTTPException(status_code=403, detail="Editing app templates is not allowed for this account.")


async def verify_persistent_storage_enabled(
    user: dict[str, Any] = Depends(verify_token),
) -> dict[str, Any]:
    """Dependency requiring persistent storage to be enabled for the user."""
    if not user.get("effective_settings", {}).get("persistent_storage", False):
        raise HTTPException(
            status_code=403, detail="Persistent storage is disabled for this account."
        )
    return user


async def verify_public_sharing_enabled(
    user: dict[str, Any] = Depends(verify_persistent_storage_enabled),
) -> dict[str, Any]:
    """Dependency requiring public sharing (admins always pass)."""
    if user.get("is_admin"):
        return user
    if user.get("effective_settings", {}).get("public_sharing", False):
        return user
    raise HTTPException(status_code=403, detail="Public file sharing is disabled for this account.")


def canonical_uuid(value: uuid.UUID | str) -> str:
    """Return the canonical `8-4-4-4-12` text form of a UUID.

    Used for values that end up in cookie names, paths, and generated HTML so
    only the validated, re-serialised form is ever used.

    Args:
        value: A UUID or its text form.

    Returns:
        The lower-case hyphenated UUID string.

    Raises:
        ValueError: If `value` is not a UUID.
    """
    return str(uuid.UUID(str(value)))


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_share_password(password: str) -> str:
    """Hash a share password with salted scrypt.

    Args:
        password: Clear-text password.

    Returns:
        `scrypt$<salt_b64>$<hash_b64>`.
    """
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_share_password(password: str, stored_hash: str) -> bool:
    """Check a password against a stored `scrypt$salt$hash` value.

    Args:
        password: Clear-text password to check.
        stored_hash: Value stored in the share metadata.

    Returns:
        `True` when the password matches.
    """
    if not stored_hash or not stored_hash.startswith("scrypt$"):
        return False
    try:
        _prefix, salt_b64, hash_b64 = stored_hash.split("$", 2)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return secrets.compare_digest(candidate, expected)


#: What a web sign-in's session answers, with session isolation, when asked for anywhere but its own origin.
OWN_ORIGIN_NEEDED = (
    "A session of a web sign-in opens on its own origin, <session id>.<domain>, apart from the web app. "
    "This server has no such name the browser reaches: it needs wildcard DNS and a certificate for it "
    "(see SEALSKIN_SESSION_DOMAIN and SEALSKIN_SESSION_ISOLATION)."
)
#: What a web sign-in's session answers on the web app's origin when this node has no copy of its web client.
WEB_CLIENT_NEEDED = (
    "A session of a web sign-in on the web app's origin is served by the server's copy of the application's "
    "web client, which this server could not export from the image (see SEALSKIN_WEB_CLIENT_PATH)."
)


def on_session_origin(request: Request, session_id: str) -> bool:
    """Whether a request reached a session on the session's own origin, whose name starts with its id.

    A session is served from one origin, the first its token was exchanged on:
    its own where the browser reaches one and `session_isolation` asks for
    it, so its pages share no storage, cookies, or service workers with the
    web app or other sessions, and the server's shared origin otherwise,
    where the pages of a web sign-in's session are the server's own copy of
    the web client (see `webclient`).
    """
    return request.headers.get("host", "").split(".", 1)[0].lower() == session_id


def relative(url: URL) -> str:
    """Return the path and query of `url`, for a redirect that stays on the origin the browser used.

    A reverse proxy in front of this node need not pass the browser's port in
    `Host`, so an absolute URL built from the request can name a place the
    browser never reached.
    """
    return url.path + (f"?{url.query}" if url.query else "")


def token_matches(given: str | None, expected: str | None) -> bool:
    """Compare a token a client sent with the stored one in constant time.

    Missing values never match. The compare runs on the UTF-8 bytes, since
    `secrets.compare_digest` refuses a `str` holding anything but ASCII.

    Args:
        given: Token from the request.
        expected: Token held in the session record.

    Returns:
        `True` when both are present and equal.
    """
    if not given or not expected:
        return False
    return secrets.compare_digest(given.encode(), expected.encode())
