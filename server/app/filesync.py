"""Keeping every user's shared files in the object store.

A session mounts its user's shared files at `Desktop/files`. With more than
one node, the store holds the copy every node works from. A session starts
without waiting for the files: the node's copy catches up in the background
as the session comes up (`pull_later`), and a launch that opens one file of
the directory fetches that file alone first (`pull_file`). What a session
changed is sent when it stops and every few minutes while it runs (`sync`).
Files are written whole and moved into place, so a running session never
sees a half-written one.

Each file is the object `files/<user>/<path>`. A node remembers the entity
tag, size, and time of every file as of its last sync, which tells a file
changed here from one changed elsewhere. The last writer wins; when both
sides changed a file, this node's version is kept beside the store's under a
`.conflict-<node>` name. Files larger than `MAX_BYTES` stay on their node.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from . import cluster, persistence, store
from .settings import settings

logger = logging.getLogger(__name__)

MAX_BYTES = 512 * 1024 * 1024
_locks: dict[str, threading.Lock] = {}
_tasks: set[asyncio.Task] = set()


def enabled() -> bool:
    """Whether shared files are synced: `files_sync` on, or `auto` with a second node in the cluster."""
    mode = settings.files_sync.lower()
    return mode == "on" or (mode == "auto" and cluster.is_clustered())


def _manifest_path(username: str) -> str:
    """Return where this node remembers a user's last sync."""
    return os.path.join(settings.node_state_path, "files", f"{username}.yml")


def _local_files(root: str) -> dict[str, tuple[int, int]]:
    """Return the size and modification time of every regular file under `root` by relative path."""
    found: dict[str, tuple[int, int]] = {}
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
        for name in files:
            path = os.path.join(directory, name)
            try:
                stat = os.lstat(path)
            except OSError:
                continue
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            found[os.path.relpath(path, root).replace(os.sep, "/")] = (stat.st_size, int(stat.st_mtime))
    return found


def _write(target: str, data: bytes, tag: str) -> dict[str, Any]:
    """Write a file whole beside its place and move it there, and return its manifest entry."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    partial = f"{target}.partial-{cluster.NODE_ID[:6]}"
    with open(partial, "wb") as handle:
        handle.write(data)
    os.replace(partial, target)
    stat = os.stat(target)
    return {"etag": tag, "size": stat.st_size, "mtime": int(stat.st_mtime)}


def _pull_file(username: str, root: str, name: str) -> bool:
    """Fetch one of a user's shared files from the store unless this node's copy is current; return whether it is here."""
    prefix = f"files/{username}/"
    target = os.path.join(root, *name.split("/"))
    if not os.path.abspath(target).startswith(os.path.abspath(root) + os.sep):
        return False
    lock = _locks.setdefault(username, threading.Lock())
    with lock:
        manifest: dict[str, dict[str, Any]] = persistence.read_yaml(_manifest_path(username), {}) or {}
        tags = store.list_keys(prefix + name)
        tag = tags.get(prefix + name)
        if tag is None:
            return os.path.isfile(target)
        known = manifest.get(name)
        if known and known["etag"] == tag and os.path.isfile(target):
            return True
        found = store.get(prefix + name)
        if found is None:
            return os.path.isfile(target)
        manifest[name] = _write(target, found[0], found[1])
        os.makedirs(os.path.dirname(_manifest_path(username)), exist_ok=True, mode=0o700)
        persistence.write_yaml_sync(_manifest_path(username), manifest)
        return True


def _sync(username: str, root: str, send: bool) -> None:
    """Bring `root` and the store's copy of a user's shared files together.

    Args:
        username: Owner of the files.
        root: The user's shared files directory on this node.
        send: Also send what changed here; a pull before a launch sends nothing.
    """
    prefix = f"files/{username}/"
    lock = _locks.setdefault(username, threading.Lock())
    with lock:
        manifest: dict[str, dict[str, Any]] = persistence.read_yaml(_manifest_path(username), {}) or {}
        remote = {key[len(prefix) :]: tag for key, tag in store.list_keys(prefix).items()}
        os.makedirs(root, exist_ok=True)
        local = _local_files(root)

        def changed_here(name: str) -> bool:
            known = manifest.get(name)
            return name in local and (not known or (known["size"], known["mtime"]) != local[name])

        for name, tag in remote.items():
            known = manifest.get(name)
            if known and known["etag"] == tag and name in local:
                continue
            if known and known["etag"] == tag and name not in local:
                if send:
                    store.delete(prefix + name)
                    manifest.pop(name, None)
                continue
            target = os.path.join(root, *name.split("/"))
            if not os.path.abspath(target).startswith(os.path.abspath(root) + os.sep):
                continue
            if changed_here(name):
                os.replace(target, f"{target}.conflict-{cluster.NODE_ID[:6]}")
            found = store.get(prefix + name)
            if found is None:
                continue
            manifest[name] = _write(target, found[0], found[1])

        for name in [n for n in manifest if n not in remote]:
            target = os.path.join(root, *name.split("/"))
            if name in local and not changed_here(name):
                os.remove(target)
                manifest.pop(name)
            elif name not in local:
                manifest.pop(name)

        if send:
            for name, (size, mtime) in _local_files(root).items():
                known = manifest.get(name)
                if known and (known["size"], known["mtime"]) == (size, mtime):
                    continue
                if size > MAX_BYTES:
                    logger.warning("Shared file '%s' of '%s' is too large to sync; it stays on this node.", name, username)
                    continue
                with open(os.path.join(root, *name.split("/")), "rb") as handle:
                    tag = store.put(prefix + name, handle.read())
                manifest[name] = {"etag": tag, "size": size, "mtime": mtime}

        os.makedirs(os.path.dirname(_manifest_path(username)), exist_ok=True, mode=0o700)
        persistence.write_yaml_sync(_manifest_path(username), manifest)


async def _run(username: str, root: str, send: bool) -> None:
    """Sync in a thread and log, rather than raise, what goes wrong."""
    if not enabled():
        return
    try:
        await asyncio.to_thread(_sync, username, root, send)
    except Exception as exc:  # noqa: BLE001 - a sync failure never fails a launch or a stop
        logger.warning("Could not sync the shared files of '%s': %s", username, exc)


async def pull(username: str, root: str) -> None:
    """Bring a user's shared files on this node up to date, taking what the store has and sending nothing."""
    await _run(username, root, send=False)


def pull_later(username: str, root: str) -> None:
    """Bring a user's shared files up to date in the background, as a session starts over them."""
    task = asyncio.create_task(pull(username, root))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def pull_file(username: str, root: str, name: str) -> bool:
    """Fetch the one shared file a launch opens, and say whether it is on this node.

    Args:
        username: Owner of the files.
        root: The user's shared files directory on this node.
        name: The file's path under the directory.
    """
    if not enabled():
        return os.path.isfile(os.path.join(root, *name.split("/")))
    try:
        return await asyncio.to_thread(_pull_file, username, root, name)
    except Exception as exc:  # noqa: BLE001 - the launch goes on with what the node has
        logger.warning("Could not fetch the shared file '%s' of '%s': %s", name, username, exc)
        return os.path.isfile(os.path.join(root, *name.split("/")))


async def sync(username: str, root: str) -> None:
    """Send what changed in a user's shared files here and take what changed elsewhere."""
    await _run(username, root, send=True)


def push_later(username: str, root: str) -> None:
    """Sync a user's shared files in the background, as when a session stops."""
    task = asyncio.create_task(sync(username, root))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
