"""Endpoints the nodes of a cluster call on each other (see `app.cluster`).

They are served on the peer listener alone, and every one but `join`, which
proves a join code instead, takes only a request signed by an approved node.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import cluster, homes, store
from .uploads import upload_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/peer", include_in_schema=False)

_NAME = re.compile(r"[a-zA-Z0-9_-]+")
_UPLOAD_FILE = re.compile(r"chunk_\d+|metadata\.json")


class JoinRequest(BaseModel):
    """A node asking to join with the proof of its join code."""

    code_id: str
    record: dict[str, Any]
    proof: str


async def peer(request: Request) -> dict[str, Any]:
    """Dependency: the approved node that signed the request."""
    return (await cluster.verify_request(request))["node"]


async def streaming_peer(request: Request) -> dict[str, Any]:
    """Dependency: the approved node that signed a request whose body it streams."""
    return (await cluster.verify_request(request, body=b""))["node"]


def _names(*values: str) -> None:
    """Refuse path segments that are not plain names."""
    if not all(_NAME.fullmatch(value) for value in values):
        raise HTTPException(status_code=400, detail="Invalid name.")


@router.get("/status")
async def status(_node: dict[str, Any] = Depends(peer)) -> dict[str, Any]:
    """Return this node's load and sessions."""
    return cluster.local_status()


@router.post("/notify", status_code=204)
async def notify(_node: dict[str, Any] = Depends(peer)) -> Response:
    """Look at the shared records now: another node changed them."""
    cluster.store_wake.set()
    return Response(status_code=204)


@router.post("/join")
async def join(req: JoinRequest, request: Request) -> dict[str, Any]:
    """Admit a node that proves a join code an administrator issued."""
    if not cluster.on_peer_listener(request):
        raise HTTPException(status_code=404)
    admitted = cluster.accept_join(req.code_id, req.record, req.proof)
    cluster.apply_registry()
    cluster.store_wake.set()
    return admitted


def _keeper() -> store.FileStore:
    """Return this node's own file store, which it serves to the nodes that joined through it."""
    current = store.get_store()
    if not isinstance(current, store.FileStore):
        raise HTTPException(status_code=409, detail="This node does not keep the shared records.")
    return current


def _shared(key: str) -> str:
    """Refuse a key that names no shared object."""
    try:
        store.path_for(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Not a shared object.") from exc
    return key


@router.get("/store/{key:path}")
async def store_get(key: str, request: Request, _node: dict[str, Any] = Depends(peer)) -> Response:
    """Return a shared object, or the entity tags under a prefix when `X-SealSkin-List` names one."""
    keeper = _keeper()
    listing = request.headers.get("x-sealskin-list")
    if listing is not None:
        from fastapi.responses import JSONResponse

        return JSONResponse(keeper.list("" if listing == "*" else listing))
    found = keeper.get(_shared(key))
    if found is None:
        raise HTTPException(status_code=404)
    return Response(content=found[0], headers={"etag": found[1]}, media_type="application/octet-stream")


@router.put("/store/{key:path}")
async def store_put(key: str, request: Request, node: dict[str, Any] = Depends(peer)) -> Response:
    """Write a shared object, on the condition the request names."""
    keeper = _keeper()
    if not cluster.may_write(node, _shared(key)):
        raise HTTPException(status_code=403, detail="This node may not write that object.")
    try:
        tag = keeper.put(
            key,
            await request.body(),
            if_match=request.headers.get("if-match"),
            if_absent=request.headers.get("if-none-match") == "*",
        )
    except store.Conflict as exc:
        raise HTTPException(status_code=412) from exc
    cluster.store_wake.set()
    return Response(status_code=200, headers={"etag": tag})


@router.delete("/store/{key:path}", status_code=204)
async def store_delete(key: str, request: Request, node: dict[str, Any] = Depends(peer)) -> Response:
    """Delete a shared object."""
    keeper = _keeper()
    if not cluster.may_write(node, _shared(key)):
        raise HTTPException(status_code=403, detail="This node may not write that object.")
    try:
        keeper.delete(key, if_match=request.headers.get("if-match"))
    except store.Conflict as exc:
        raise HTTPException(status_code=412) from exc
    cluster.store_wake.set()
    return Response(status_code=204)


@router.put("/upload/{username}/{upload_id}/{name}", status_code=204)
async def take_upload_chunk(
    username: str, upload_id: str, name: str, request: Request, _node: dict[str, Any] = Depends(peer)
) -> Response:
    """Take one file of an upload a frontend received for a request it forwards here."""
    _names(username)
    if not _UPLOAD_FILE.fullmatch(name):
        raise HTTPException(status_code=400, detail="Not a file of an upload.")
    directory = upload_path(username, upload_id)
    os.makedirs(directory, exist_ok=True, mode=0o700)
    with open(os.path.join(directory, name), "wb") as handle:
        handle.write(await request.body())
    return Response(status_code=204)


@router.put("/homes/{username}/{home_name}")
async def take_home(
    username: str, home_name: str, request: Request, _node: dict[str, Any] = Depends(streaming_peer)
) -> dict[str, str]:
    """Unpack a home directory another node moves here."""
    _names(username, home_name)
    await homes.receive(username, home_name, request.stream())
    return {"status": "success"}


@router.delete("/homes/{username}/{home_name}", status_code=204)
async def drop_home(username: str, home_name: str, _node: dict[str, Any] = Depends(peer)) -> Response:
    """Remove a home directory received in a move that failed afterwards."""
    _names(username, home_name)
    if cluster.node_of_home(username, home_name) == cluster.NODE_ID:
        raise HTTPException(status_code=409, detail="That home directory lives here.")
    homes.discard(username, home_name)
    return Response(status_code=204)
