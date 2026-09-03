"""E2EE handshake endpoints (the only unencrypted API routes besides /ui)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..models import (
    HandshakeExchangeRequest,
    HandshakeExchangeResponse,
    HandshakeInitiateResponse,
)
from ..security import register_crypto_session, sign_nonce, unwrap_session_key

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/handshake/initiate", response_model=HandshakeInitiateResponse)
async def handshake_initiate() -> dict[str, str]:
    """Return a signed nonce so the client can authenticate the server."""
    nonce, signature = sign_nonce()
    return {"nonce": nonce, "signature": signature}


@router.post("/api/handshake/exchange", response_model=HandshakeExchangeResponse)
async def handshake_exchange(request: HandshakeExchangeRequest) -> dict[str, str]:
    """Accept the client's wrapped AES key and open an E2EE session."""
    try:
        aes_key = unwrap_session_key(request.encrypted_session_key)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to decrypt session key during handshake: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to decrypt session key") from exc
    session_id = register_crypto_session(aes_key)
    logger.info("E2EE handshake successful. New crypto session: %s...", session_id[:8])
    return {"session_id": session_id}
