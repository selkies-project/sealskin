"""Atomic YAML persistence with per-file locks and change watching.

SealSkin keeps its configuration in hand-editable YAML files. This module is
the single place that reads and writes them so every writer gets:

* atomic replacement (temporary file in the same directory + ``os.replace``),
* one :class:`asyncio.Lock` per path so concurrent handlers never interleave,
* a content hash of the last write so the file watcher can tell our own writes
  apart from edits made by an administrator.
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

logger = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_WRITTEN: dict[str, str] = {}


def lock_for(path: str) -> asyncio.Lock:
    """Return the lock guarding ``path``, creating it on first use.

    Args:
        path: File path (normalised with :func:`os.path.abspath`).

    Returns:
        The :class:`asyncio.Lock` shared by every writer of that file.
    """
    key = os.path.abspath(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def content_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def read_yaml(path: str, default: Any = None) -> Any:
    """Load a YAML file.

    Args:
        path: File to read.
        default: Value returned when the file does not exist or is empty.

    Returns:
        The parsed document, or ``default``.

    Raises:
        yaml.YAMLError: If the file contains invalid YAML.
        OSError: If the file exists but cannot be read.
    """
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return default if data is None else data


def dump_yaml(data: Any) -> str:
    """Serialise ``data`` the way every SealSkin file is written.

    Args:
        data: Any YAML-serialisable structure.

    Returns:
        YAML text with keys in insertion order.
    """
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def write_yaml_sync(path: str, data: Any) -> None:
    """Atomically write ``data`` to ``path`` as YAML.

    The document is written to a temporary file in the target directory and
    moved into place with :func:`os.replace`, so readers never observe a
    partially written file.

    Args:
        path: Destination file.
        data: YAML-serialisable structure.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    text = dump_yaml(data)
    encoded = text.encode("utf-8")
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


async def write_yaml(path: str, data: Any) -> None:
    """Atomically write ``data`` to ``path`` under the file's lock.

    Args:
        path: Destination file.
        data: YAML-serialisable structure.
    """
    async with lock_for(path):
        await asyncio.to_thread(write_yaml_sync, path, data)


def was_written_by_us(path: str) -> bool:
    """Tell whether the current contents of ``path`` match our last write.

    Args:
        path: File to check.

    Returns:
        ``True`` when the on-disk content hash equals the hash recorded by
        :func:`write_yaml_sync`, meaning a watcher event was caused by the
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
