"""A chunked upload holds no more than it declared, and a user holds a bounded number of them."""

import base64
import os
import time

import pytest
from fastapi import HTTPException

from app.routers import uploads

ALICE = {"username": "alice", "effective_settings": {}}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def start(size: int, user=ALICE) -> str:
    return (await uploads.upload_initiate({"filename": "f.bin", "total_size": size}, user))["upload_id"]


async def send(upload_id: str, index: int, data: bytes) -> None:
    await uploads.upload_chunk({"upload_id": upload_id, "chunk_index": index, "chunk_data_b64": b64(data)}, ALICE)


async def refused(call) -> int:
    with pytest.raises(HTTPException) as caught:
        await call
    return caught.value.status_code


async def test_the_chunks_hold_no_more_than_the_upload_declared():
    upload = await start(10)
    await send(upload, 0, b"x" * 6)
    await send(upload, 0, b"y" * 6)  # sent again, it replaces itself
    assert await refused(send(upload, 1, b"z" * 6)) == 413
    await send(upload, 1, b"z" * 4)
    big = await start(64 * 1024 * 1024)
    assert await refused(send(big, 0, b"q" * (uploads.MAX_CHUNK_BYTES + 1))) == 413


async def test_a_user_holds_a_bounded_number_of_uploads_and_old_ones_go():
    opened = [await start(1) for _ in range(uploads.MAX_OPEN_UPLOADS)]
    assert await refused(start(1)) == 429
    stale = uploads.upload_path("alice", opened[0])
    old = time.time() - uploads.UPLOAD_TTL_SECONDS - 60
    os.utime(stale, (old, old))
    assert await start(1)
    assert not os.path.exists(stale)


async def test_a_declared_size_past_the_storage_limit_is_refused():
    limited = {"username": "bob", "effective_settings": {"storage_limit": 1}}
    assert await refused(start(2 * 1024**3, limited)) == 413
    assert await refused(start(-1, limited)) == 413
    assert await start(1024, limited)
