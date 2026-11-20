"""Keeping every user's shared files in the object store.

A session mounts its user's shared files at `Desktop/files`. With more than
one node, the store holds the copy every node works from. A session starts
without waiting for the files: the node's copy catches up in the background
as the session comes up (`pull_later`), and a launch that opens one file of
the directory fetches that file alone first (`pull_file`). What a session
changed is sent when it stops and every few minutes while it runs (`sync`).
Files are written whole and moved into place, so a running session never
sees a half-written one, and never through a symbolic link: a user's link
in the directory, which a session can make, is not followed (`_place`).

Each file is the object `files/<user>/<path>`, encrypted under the user's
key when the cluster has one (`filecrypt`). A node remembers the entity
tag, size, and time of every file as of its last sync, which tells a file
changed here from one changed elsewhere. The last writer wins; when both
sides changed a file, this node's version is kept beside the store's under a
`.conflict-<node>` name. Files larger than `MAX_BYTES` stay on their node,
and a file this node cannot decrypt is left as it is on both sides.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat as stat_module
import threading
from typing import Any

from . import cluster, filecrypt, persistence, storage_usage, store
from .fsutil import open_within
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


def _place(root: str, name: str) -> str | None:
    """Return where the shared file `name` goes under `root`, or `None` when the path leaves the directory or crosses a link.

    The directory is a session's to write, so a link in it may point
    anywhere; a file is placed only where every directory on its way is a
    real one under the root, and never over a link.
    """
    base = os.path.realpath(root)
    target = os.path.normpath(os.path.join(base, *name.split("/")))
    parent = os.path.dirname(target)
    if not (parent == base or parent.startswith(base + os.sep)):
        return None
    if os.path.realpath(parent) != parent or os.path.islink(target):
        return None
    return target


def _write(root: str, target: str, data: bytes, tag: str, keep_as: str = "") -> dict[str, Any]:
    """Write a file whole beside its place and move it there, and return its manifest entry.

    The directory is opened and checked inside `root` (`open_within`), and
    everything after happens within that descriptor: a link a session swaps
    in for a directory on the way, after `_place` looked, takes nothing out
    of the user's files.

    Args:
        root: The user's shared files directory.
        target: Where `_place` put the file.
        data: Its content.
        tag: The entity tag of the object it came from.
        keep_as: A name suffix to keep the file this one replaces under.

    Raises:
        ValueError: When the file's directory turned out to lie outside `root`.
    """
    os.makedirs(os.path.dirname(target), exist_ok=True)
    name = os.path.basename(target)
    partial = f"{name}.partial-{cluster.NODE_ID}"
    dir_fd = open_within(os.path.realpath(root), os.path.dirname(target), os.O_RDONLY | os.O_DIRECTORY)
    try:
        if keep_as:
            os.replace(name, name + keep_as, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        try:
            os.unlink(partial, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(partial, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        stat = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    finally:
        os.close(dir_fd)
    return {"etag": tag, "size": stat.st_size, "mtime": int(stat.st_mtime)}


def _read(root: str, name: str) -> bytes | None:
    """Return the content of the shared file `name`, or `None` when it is no regular file inside `root` any more or too large."""
    try:
        fd = open_within(os.path.realpath(root), os.path.join(root, *name.split("/")))
    except (OSError, ValueError):
        return None
    with os.fdopen(fd, "rb") as handle:
        status = os.fstat(handle.fileno())
        if not stat_module.S_ISREG(status.st_mode) or status.st_size > MAX_BYTES:
            return None
        return handle.read(MAX_BYTES + 1)[:MAX_BYTES]


def _pull_file(username: str, root: str, name: str) -> tuple[bool, int]:
    """Fetch one of a user's shared files from the store unless this node's copy is current.

    Returns:
        Whether the file is here, and the bytes this call placed.
    """
    prefix = f"files/{username}/"
    target = _place(root, name)
    if target is None:
        logger.warning("Shared file '%s' of '%s' is not placed: its path leaves the directory or crosses a link.", name, username)
        return False, 0
    lock = _locks.setdefault(username, threading.Lock())
    with lock:
        manifest: dict[str, dict[str, Any]] = persistence.read_yaml(_manifest_path(username), {}) or {}
        tags = store.list_keys(prefix + name)
        tag = tags.get(prefix + name)
        if tag is None:
            return os.path.isfile(target), 0
        known = manifest.get(name)
        if known and known["etag"] == tag and os.path.isfile(target):
            return True, 0
        found = store.get(prefix + name)
        if found is None:
            return os.path.isfile(target), 0
        try:
            content = filecrypt.unseal(username, found[0])
        except filecrypt.Unreadable as exc:
            logger.warning("Shared file '%s' of '%s' is not readable on this node: %s", name, username, exc)
            return os.path.isfile(target), 0
        manifest[name] = _write(root, target, content, found[1])
        os.makedirs(os.path.dirname(_manifest_path(username)), exist_ok=True, mode=0o700)
        persistence.write_yaml_sync(_manifest_path(username), manifest)
        return True, len(content)


def _sync(username: str, root: str, send: bool) -> bool:
    """Bring `root` and the store's copy of a user's shared files together.

    Args:
        username: Owner of the files.
        root: The user's shared files directory on this node.
        send: Also send what changed here; a pull before a launch sends nothing.

    Returns:
        Whether a file was placed in or removed from `root`.
    """
    prefix = f"files/{username}/"
    lock = _locks.setdefault(username, threading.Lock())
    with lock:
        manifest: dict[str, dict[str, Any]] = persistence.read_yaml(_manifest_path(username), {}) or {}
        remote = {key[len(prefix) :]: tag for key, tag in store.list_keys(prefix).items()}
        os.makedirs(root, exist_ok=True)
        local = _local_files(root)
        refused: list[str] = []
        touched = False

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
            target = _place(root, name)
            if target is None:
                refused.append(name)
                continue
            found = store.get(prefix + name)
            if found is None:
                continue
            try:
                content = filecrypt.unseal(username, found[0])
            except filecrypt.Unreadable as exc:
                logger.warning("Shared file '%s' of '%s' is not readable on this node: %s", name, username, exc)
                continue
            try:
                manifest[name] = _write(
                    root, target, content, found[1], keep_as=f".conflict-{cluster.NODE_ID}" if changed_here(name) else ""
                )
                touched = True
            except ValueError:
                refused.append(name)

        for name in [n for n in manifest if n not in remote]:
            target = _place(root, name)
            if name in local and target and not changed_here(name):
                os.remove(target)
                manifest.pop(name)
                touched = True
            elif name not in local:
                manifest.pop(name)
        if refused:
            logger.warning(
                "%d shared file(s) of '%s' were not placed, their paths leave the directory or cross a link: %s",
                len(refused),
                username,
                ", ".join(sorted(refused)[:5]),
            )

        if send:
            for name, (size, mtime) in _local_files(root).items():
                known = manifest.get(name)
                if known and (known["size"], known["mtime"]) == (size, mtime):
                    continue
                if size > MAX_BYTES:
                    logger.warning("Shared file '%s' of '%s' is too large to sync; it stays on this node.", name, username)
                    continue
                content = _read(root, name)
                if content is None:
                    continue
                tag = store.put(prefix + name, filecrypt.seal(username, content))
                manifest[name] = {"etag": tag, "size": size, "mtime": mtime}

        os.makedirs(os.path.dirname(_manifest_path(username)), exist_ok=True, mode=0o700)
        persistence.write_yaml_sync(_manifest_path(username), manifest)
    return touched


async def _run(username: str, root: str, send: bool) -> None:
    """Sync in a thread and log, rather than raise, what goes wrong."""
    if not enabled():
        return
    try:
        touched = await asyncio.to_thread(_sync, username, root, send)
    except Exception as exc:  # noqa: BLE001 - a sync failure never fails a launch or a stop
        logger.warning("Could not sync the shared files of '%s': %s", username, exc)
        touched = True
    if touched:
        storage_usage.changed(root)


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
        here, placed = await asyncio.to_thread(_pull_file, username, root, name)
    except Exception as exc:  # noqa: BLE001 - the launch goes on with what the node has
        logger.warning("Could not fetch the shared file '%s' of '%s': %s", name, username, exc)
        return os.path.isfile(os.path.join(root, *name.split("/")))
    storage_usage.stored(root, placed)
    return here


def forget(username: str) -> None:
    """Remove a deleted user's shared files from the store, and the key they were encrypted under."""
    prefix = f"files/{username}/"
    for key in store.list_keys(prefix):
        store.delete(key)
    filecrypt.forget(username)
    try:
        os.remove(_manifest_path(username))
    except FileNotFoundError:
        pass


async def sync(username: str, root: str) -> None:
    """Send what changed in a user's shared files here and take what changed elsewhere."""
    await _run(username, root, send=True)


def push_later(username: str, root: str) -> None:
    """Sync a user's shared files in the background, as when a session stops."""
    task = asyncio.create_task(sync(username, root))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
