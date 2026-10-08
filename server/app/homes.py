"""Moving a home directory from the node that holds it to another.

A home directory lives on one node, and a session that mounts it starts
there. `migrate` runs on the node that holds the directory: it refuses while
a session has it mounted, streams it to the other node as a tar archive over
the peer channel, and only when that node has unpacked all of it records the
new location and deletes its own copy.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException

from . import cluster
from .fsutil import safe_join
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

CHUNK = 1024 * 1024
_moving: set[tuple[str, str]] = set()


def home_path(username: str, home_name: str) -> str:
    """Return the directory of a user's home on this node.

    Raises:
        HTTPException: 400 for a name that steps out of the user's storage.
    """
    try:
        return safe_join(settings.storage_path, username, home_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid home directory name.") from exc


def is_mounted(username: str, home_name: str) -> bool:
    """Whether a running session on this node has the home directory mounted."""
    path = os.path.realpath(home_path(username, home_name))
    return any(
        data.get("host_mount_path") and os.path.realpath(data["host_mount_path"]) == path
        for data in state.sessions.values()
    )


async def _archive(username: str, home_name: str, failed: list[str]) -> AsyncIterator[bytes]:
    """Yield a tar archive of a home directory, noting in `failed` when tar did not finish cleanly."""
    process = await asyncio.create_subprocess_exec(
        "tar",
        "-C",
        os.path.dirname(home_path(username, home_name)),
        "-cf",
        "-",
        home_name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        while chunk := await process.stdout.read(CHUNK):
            yield chunk
        if await process.wait() != 0:
            failed.append((await process.stderr.read()).decode(errors="replace")[-300:])
    finally:
        if process.returncode is None:
            process.kill()


async def migrate(username: str, home_name: str, target: str) -> dict[str, str]:
    """Move a home directory on this node to the node `target`.

    Returns:
        `{"status": "success", "node": <target id>}`.

    Raises:
        HTTPException: 404 when the directory is not here, 409 while it is in
            use or already moving, 400 for a target that cannot take it, 502
            when the transfer fails, in which case nothing changed.
    """
    path = home_path(username, home_name)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail=f"Home directory '{home_name}' is not on this node.")
    if target == cluster.NODE_ID:
        raise HTTPException(status_code=400, detail="The home directory is on that node already.")
    if target not in cluster.members(role="runtime"):
        raise HTTPException(status_code=400, detail="That node runs no sessions or is not answering.")
    if is_mounted(username, home_name):
        raise HTTPException(status_code=409, detail="Stop the sessions using this home directory first.")
    if (username, home_name) in _moving:
        raise HTTPException(status_code=409, detail="This home directory is being moved already.")
    _moving.add((username, home_name))
    try:
        failed: list[str] = []
        try:
            answer = await cluster.call(
                target,
                "PUT",
                f"/peer/homes/{username}/{home_name}",
                stream=_archive(username, home_name, failed),
                timeout=6 * 3600,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"The transfer failed: {exc}") from exc
        if failed or answer.status_code != 200:
            reason = failed[0] if failed else answer.text[:300]
            if answer.status_code == 200:
                await cluster.call(target, "DELETE", f"/peer/homes/{username}/{home_name}")
            raise HTTPException(status_code=502, detail=f"The transfer failed: {reason}")
        await asyncio.to_thread(cluster.record_home, username, home_name, target)
        await asyncio.to_thread(shutil.rmtree, path, True)
        await cluster.announce()
        logger.info("Moved home directory '%s' of '%s' to node %s.", home_name, username, target)
        return {"status": "success", "node": target}
    finally:
        _moving.discard((username, home_name))


async def receive(username: str, home_name: str, chunks: AsyncIterator[bytes]) -> None:
    """Unpack the archive of a home directory another node sends, in place only when all of it arrived.

    Raises:
        HTTPException: 409 when this node has a directory of that name, 500
            when the archive does not unpack.
    """
    path = home_path(username, home_name)
    if os.path.exists(path):
        raise HTTPException(status_code=409, detail="This node already has a home directory of that name.")
    staging = os.path.join(os.path.dirname(path), f".incoming-{home_name}")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, mode=0o700)
    process = await asyncio.create_subprocess_exec(
        "tar", "-C", staging, "-xpf", "-", stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        async for chunk in chunks:
            process.stdin.write(chunk)
            await process.stdin.drain()
        process.stdin.close()
        problem = (await process.stderr.read()).decode(errors="replace")
        unpacked = os.path.join(staging, home_name)
        if await process.wait() != 0 or not os.path.isdir(unpacked):
            raise HTTPException(status_code=500, detail=f"The archive did not unpack: {problem[-300:]}")
        os.replace(unpacked, path)
    except (BrokenPipeError, ConnectionResetError) as exc:
        raise HTTPException(status_code=500, detail="The archive did not unpack.") from exc
    finally:
        if process.returncode is None:
            process.kill()
        shutil.rmtree(staging, ignore_errors=True)


def discard(username: str, home_name: str) -> None:
    """Remove a home directory this node received in a move that then failed."""
    shutil.rmtree(home_path(username, home_name), ignore_errors=True)
