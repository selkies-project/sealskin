"""Atomic YAML persistence with per-file locks and change watching.

SealSkin keeps its configuration in hand-editable YAML files. This module is
the single place that reads and writes them so every writer gets:

* atomic replacement (temporary file in the same directory + `os.replace`),
* one `Lock` per path so concurrent handlers never interleave,
* a content hash of the last write so the file watcher can tell our own writes
  apart from edits made by an administrator.

A path under one of `store.MOUNTS` names an object the nodes of a cluster
share. While the store is this node's own files that changes nothing; with a
remote store those reads and writes go to it instead, each write conditional
on the object being as this node last read it, so two nodes never overwrite
each other unseen (`store.Conflict`). `watch_store` then stands in for the
file watcher.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

import yaml

from . import store

logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_WRITTEN: dict[str, str] = {}
_READ_TAGS: dict[str, str] = {}


def lock_for(path: str) -> asyncio.Lock:
    """Return the lock guarding `path`, creating it on first use.

    Args:
        path: File path (normalised with `abspath`).

    Returns:
        The `Lock` shared by every writer of that file.
    """
    key = os.path.abspath(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def content_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of `data`."""
    return hashlib.sha256(data).hexdigest()


def _shared_key(path: str) -> str | None:
    """Return the key of `path` in a remote store, or `None` for a file read and written in place."""
    return None if store.is_local() else store.key_for(path)


def read_bytes(path: str) -> bytes | None:
    """Return the content of a file or shared object, or `None` when it does not exist."""
    key = _shared_key(path)
    if key:
        found = store.get(key)
        _READ_TAGS.pop(key, None)
        if found is None:
            return None
        _READ_TAGS[key] = found[1]
        return found[0]
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def write_bytes(path: str, data: bytes) -> None:
    """Atomically write a file, or a shared object as this node last read it.

    Raises:
        store.Conflict: When another node changed the shared object since.
    """
    key = _shared_key(path)
    if key:
        known = _READ_TAGS.get(key)
        _READ_TAGS[key] = store.put(key, data, if_match=known, if_absent=known is None)
        _LAST_WRITTEN[os.path.abspath(path)] = content_hash(data)
        return
    _write_file(path, data)


def remove(path: str) -> None:
    """Delete a file or shared object; a missing one is not an error."""
    key = _shared_key(path)
    if key:
        store.delete(key)
        _READ_TAGS.pop(key, None)
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def exists(path: str) -> bool:
    """Whether a file or shared object exists."""
    key = _shared_key(path)
    if key:
        return key in store.list_keys(key)
    return os.path.isfile(path)


def list_names(directory: str) -> list[str]:
    """Return the names of the files or shared objects directly inside `directory`, sorted."""
    key = _shared_key(directory)
    if key:
        return store.children(key if key.endswith("/") else key + "/")
    try:
        return sorted(
            name
            for name in os.listdir(directory)
            if not name.startswith(".") and os.path.isfile(os.path.join(directory, name))
        )
    except (FileNotFoundError, NotADirectoryError):
        return []


def read_yaml(path: str, default: Any = None) -> Any:
    """Load a YAML file.

    Args:
        path: File to read.
        default: Value returned when the file does not exist or is empty.

    Returns:
        The parsed document, or `default`.

    Raises:
        yaml.YAMLError: If the file contains invalid YAML.
        OSError: If the file exists but cannot be read.
    """
    raw = read_bytes(path)
    if raw is None:
        return default
    data = yaml.safe_load(raw.decode("utf-8"))
    return default if data is None else data


def dump_yaml(data: Any) -> str:
    """Serialise `data` the way every SealSkin file is written.

    Args:
        data: Any YAML-serialisable structure.

    Returns:
        YAML text with keys in insertion order.
    """
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _write_file(path: str, encoded: bytes) -> None:
    """Replace `path` with `encoded` through a temporary file in its directory."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".yml")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(path):
            try:
                os.chmod(temp_path, os.stat(path).st_mode & 0o777)
            except OSError:
                pass
        os.replace(temp_path, path)
        _LAST_WRITTEN[os.path.abspath(path)] = content_hash(encoded)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def write_yaml_sync(path: str, data: Any) -> None:
    """Atomically write `data` to `path` as YAML.

    The document is written to a temporary file in the target directory and
    moved into place with `replace`, so readers never observe a
    partially written file.

    Args:
        path: Destination file.
        data: YAML-serialisable structure.

    Raises:
        store.Conflict: When `path` is a shared object another node changed.
    """
    write_bytes(path, dump_yaml(data).encode("utf-8"))


async def write_yaml(path: str, data: Any) -> None:
    """Atomically write `data` to `path` under the file's lock.

    Args:
        path: Destination file.
        data: YAML-serialisable structure.
    """
    async with lock_for(path):
        await asyncio.to_thread(write_yaml_sync, path, data)


def was_written_by_us(path: str) -> bool:
    """Tell whether the current contents of `path` match our last write.

    Args:
        path: File to check.

    Returns:
        `True` when the on-disk content hash equals the hash recorded by
        `write_yaml_sync`, meaning a watcher event was caused by the
        server itself rather than by an external edit.
    """
    key = os.path.abspath(path)
    last = _LAST_WRITTEN.get(key)
    if last is None:
        return False
    try:
        with open(path, "rb") as handle:
            return content_hash(handle.read()) == last
    except OSError:
        return False


ReloadCallback = Callable[[str], Awaitable[None]]


async def watch_paths(
    targets: dict[str, ReloadCallback],
    stop_event: asyncio.Event,
    debounce_ms: int = 500,
) -> None:
    """Watch files and directories and call a callback when they change.

    Args:
        targets: Mapping of path (file or directory) to the coroutine function
            invoked with the changed path. Directory targets fire for any file
            inside them.
        stop_event: Set it to end the watch loop.
        debounce_ms: Quiet period before a batch of changes is reported.
    """
    try:
        from watchfiles import awatch
    except ImportError:  # pragma: no cover - watchfiles is a hard dependency
        logger.warning("watchfiles is not installed; configuration reload on edit is disabled.")
        return

    watch_roots: list[str] = []
    for target in targets:
        root = target if os.path.isdir(target) else os.path.dirname(target)
        os.makedirs(root, exist_ok=True)
        if root not in watch_roots:
            watch_roots.append(root)

    logger.info("Watching configuration paths for changes: %s", ", ".join(sorted(targets)))
    try:
        async for changes in awatch(
            *watch_roots, stop_event=stop_event, debounce=debounce_ms, step=200
        ):
            changed_paths = {os.path.abspath(path) for _change, path in changes}
            for target, callback in targets.items():
                abs_target = os.path.abspath(target)
                hit = any(
                    path == abs_target or path.startswith(abs_target + os.sep)
                    for path in changed_paths
                )
                if not hit:
                    continue
                if os.path.isfile(abs_target) and was_written_by_us(abs_target):
                    continue
                if os.path.basename(abs_target).startswith(".tmp-"):
                    continue
                try:
                    await callback(target)
                except Exception as exc:  # noqa: BLE001 - keep the watcher alive
                    logger.error("Reload callback for '%s' failed: %s", target, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Configuration watcher stopped unexpectedly: %s", exc)


async def watch_store(
    targets: dict[str, ReloadCallback],
    stop_event: asyncio.Event,
    interval: float = 15.0,
    wake: asyncio.Event | None = None,
) -> None:
    """Poll a remote store and call a callback when another node changed its objects.

    Args:
        targets: The same mapping `watch_paths` takes; each path is matched by
            its key in the store.
        stop_event: Set it to end the loop.
        interval: Seconds between two polls.
        wake: Set it to poll at once, as when a peer announces a change.
    """
    keys = {target: store.key_for(target) for target in targets}
    logger.info("Polling the object store for changes every %.0f s.", interval)
    while not stop_event.is_set():
        try:
            changed = await asyncio.to_thread(store.changed)
        except Exception as exc:  # noqa: BLE001 - keep the watcher alive
            logger.error("Could not list the object store: %s", exc)
            changed = set()
        for target, callback in targets.items():
            key = keys[target]
            if not key or not any(name == key or name.startswith(key.rstrip("/") + "/") for name in changed):
                continue
            for name in changed:
                _READ_TAGS.pop(name, None)
            try:
                await callback(target)
            except Exception as exc:  # noqa: BLE001
                logger.error("Reload callback for '%s' failed: %s", target, exc)
        waiters = [asyncio.ensure_future(stop_event.wait())]
        if wake is not None:
            waiters.append(asyncio.ensure_future(wake.wait()))
        await asyncio.wait(waiters, timeout=interval, return_when=asyncio.FIRST_COMPLETED)
        for waiter in waiters:
            waiter.cancel()
        if wake is not None:
            wake.clear()
