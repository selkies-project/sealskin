"""E2EE handshake endpoints, the routes a client reaches before it has a session key."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ..models import (
    HandshakeExchangeRequest,
    HandshakeExchangeResponse,
    HandshakeInitiateResponse,
)
from ..security import register_crypto_session, sign_nonce, unwrap_session_key

logger = logging.getLogger(__name__)
router = APIRouter()

#: The most an exchange's body may hold: a wrapped AES key is a few hundred bytes, and the
#: route answers anyone, so nothing larger is read off the connection.
HANDSHAKE_BODY_MAX = 16 * 1024


async def _bounded_body(request: Request) -> bytes:
    """The request's body, refused with 413 past `HANDSHAKE_BODY_MAX` before more is read."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > HANDSHAKE_BODY_MAX:
        raise HTTPException(status_code=413, detail="Handshake body too large.")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > HANDSHAKE_BODY_MAX:
            raise HTTPException(status_code=413, detail="Handshake body too large.")
    return bytes(body)


@router.post("/api/handshake/initiate", response_model=HandshakeInitiateResponse)
async def handshake_initiate() -> dict[str, str]:
    """Return a signed nonce so the client can authenticate the server."""
    nonce, signature = sign_nonce()
    return {"nonce": nonce, "signature": signature}


@router.post("/api/handshake/exchange", response_model=HandshakeExchangeResponse)
async def handshake_exchange(raw: Request) -> dict[str, str]:
    """Accept the client's wrapped AES key and open an E2EE session."""
    try:
        request = HandshakeExchangeRequest.model_validate(json.loads(await _bounded_body(raw)))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Invalid handshake body.") from exc
    try:
        aes_key = unwrap_session_key(request.encrypted_session_key)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to decrypt session key during handshake: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to decrypt session key") from exc
    session_id = register_crypto_session(aes_key)
    logger.info("E2EE handshake successful. New crypto session: %s...", session_id[:8])
    return {"session_id": session_id}
