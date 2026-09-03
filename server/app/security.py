"""Authentication and end-to-end encryption.

* The E2EE handshake: the server signs a nonce with its RSA key so the client
  can verify it, then the client sends an RSA-OAEP wrapped AES-256-GCM key.
* :class:`EncryptedRoute` encrypts every JSON response with the session key and
  :func:`get_decrypted_request_body` decrypts request bodies.
* :func:`verify_token` validates client-signed RS256 JWTs against the public
  key stored for the user.
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

from . import user_manager
from .models import EncryptedPayload
from .settings import settings
from .state import CryptoSession, state

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
JWT_LEEWAY_SECONDS = 60
HANDSHAKE_PATHS = ("/api/handshake/initiate", "/api/handshake/exchange")


def init_server_keys() -> None:
    """Load the server RSA key into ``state`` and publish the public PEM.

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
        ``(nonce_b64, signature_b64)`` using RSA-PSS with SHA-256.
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
    """Drop E2EE sessions idle longer than ``crypto_session_ttl_seconds``.

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
    """Return the crypto session for ``session_id`` and mark it as used."""
    session = state.crypto_sessions.get(session_id)
    if session:
        session.last_used = time.time()
    return session


async def get_decrypted_request_body(request: Request) -> dict[str, Any]:
    """FastAPI dependency returning the decrypted JSON body of a request.

    Args:
        request: Incoming request carrying ``X-Session-ID`` and an
            :class:`EncryptedPayload` body.

    Returns:
        The decrypted JSON document.

    Raises:
        HTTPException: 400 when the session is unknown or decryption fails.
    """
    session_id = request.headers.get("X-Session-ID", "")
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
    """Drop cached responses older than :data:`IDEMPOTENCY_TTL_SECONDS`."""
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

    Non-GET requests carrying an ``X-Idempotency-Key`` header are executed at
    most once per crypto session: a retried request (for example after the
    client's network blipped while a container was being created) receives
    the stored result instead of running the handler again. While the first
    attempt is still running, the retry waits for it.
    """

    def get_route_handler(self) -> Callable:
        """Wrap the original handler so JSON responses are encrypted."""
        original_handler = super().get_route_handler()

        async def run_and_capture(request: Request) -> tuple[int, bytes, str]:
            response = await original_handler(request)
            content_type = response.headers.get("content-type", "")
            body = response.body if hasattr(response, "body") else b""
            return response.status_code, body, content_type

        async def custom_handler(request: Request) -> Response:
            session_id = request.headers.get("X-Session-ID", "")
            session = _touch_session(session_id) if session_id else None
            if request.url.path not in HANDSHAKE_PATHS and not session:
                logger.warning(
                    "Security: Request to %s has invalid/missing session key.", request.url.path
                )

            idem_key = request.headers.get("X-Idempotency-Key", "").strip()
            cache_key = f"{session_id}:{request.method}:{request.url.path}:{idem_key}"
            use_idempotency = bool(session and idem_key and request.method != "GET")

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
            if not (is_json and body):
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
        The ``notAfter`` timestamp, or ``None`` if the file is missing or
        cannot be parsed.
    """
    try:
        with open(cert_path, "rb") as handle:
            cert = x509.load_pem_x509_certificate(handle.read())
        return cert.not_valid_after_utc.timestamp()
    except (OSError, ValueError) as exc:
        logger.warning("Could not read proxy certificate %s: %s", cert_path, exc)
        return None


async def verify_token(req: Request) -> dict[str, Any]:
    """FastAPI dependency authenticating a client-signed JWT.

    The token's ``sub`` claim names the user; the signature is verified with
    the public key stored for that user and ``exp`` is required.

    Args:
        req: Incoming request with an ``Authorization: Bearer`` header.

    Returns:
        The user record including ``effective_settings`` and ``group``.

    Raises:
        HTTPException: 401 for invalid tokens, 403 for inactive accounts.
    """
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
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
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token.")

    effective_settings = user_manager.get_effective_settings(username)
    is_active = user.get("is_admin") or effective_settings.get("active", False)
    if not is_active:
        raise HTTPException(status_code=403, detail="User account is inactive.")
    try:
        jwt.decode(
            token,
            user["public_key"],
            algorithms=[ALGORITHM],
            options={"require": ["exp"]},
            leeway=JWT_LEEWAY_SECONDS,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=500, detail="Server configuration error for user."
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token signature or claims.") from exc

    user = dict(user)
    user["effective_settings"] = effective_settings
    user["group"] = effective_settings.get("group", "none")
    return user


async def verify_admin(user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """Dependency requiring an administrator."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


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


_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_share_password(password: str) -> str:
    """Hash a share password with salted scrypt.

    Args:
        password: Clear-text password.

    Returns:
        ``scrypt$<salt_b64>$<hash_b64>``.
    """
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_share_password(password: str, stored_hash: str) -> bool:
    """Check a password against a stored hash.

    Supports the current ``scrypt$salt$hash`` format and the legacy unsalted
    SHA-256 hex digest.

    Args:
        password: Clear-text password to check.
        stored_hash: Value stored in the share metadata.

    Returns:
        ``True`` when the password matches.
    """
    if not stored_hash:
        return False
    if stored_hash.startswith("scrypt$"):
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
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return secrets.compare_digest(legacy, stored_hash)
