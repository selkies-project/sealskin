"""Keeping every user's shared files in the object store.

A session mounts its user's shared files at `Desktop/files`. With more than
one node, the store holds the copy every node works from: a node brings its
copy up to date before a session starts (`pull`) and sends what the session
changed when it stops and every few minutes while it runs (`sync`).

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
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(found[0])
            stat = os.stat(target)
            manifest[name] = {"etag": found[1], "size": stat.st_size, "mtime": int(stat.st_mtime)}

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
    """Bring a user's shared files on this node up to date before a session mounts them."""
    await _run(username, root, send=False)


async def sync(username: str, root: str) -> None:
    """Send what changed in a user's shared files here and take what changed elsewhere."""
    await _run(username, root, send=True)


def push_later(username: str, root: str) -> None:
    """Sync a user's shared files in the background, as when a session stops."""
    task = asyncio.create_task(sync(username, root))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
