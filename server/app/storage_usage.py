"""How much each user's storage directory on this node holds, measured rarely.

A measure walks every file under the directory: hundreds of thousands in a
home a desktop has used, seconds of seeking on a spinning disk. So walks are
made one at a time on the node, on a thread of their own, and a measure is
kept for `MEASURE_SECONDS`, in `storage_usage.yml` under `node_state_path`
as well, so that a restart does not throw the measures away.

A storage limit asks for a current measure and waits for a walk when the one
it has is older than that; anything else, the profile, is answered with the
old measure while the next walk runs. A walk somebody waits on goes before
those taken in the background.

Between walks, the bytes the server itself places in a user's storage are
added to the measure (`stored`), and anything else it changes there, a
removal, a home template's copy, a home arriving from another node, drops the
measure (`changed`), so the next ask walks again. What a running session
writes is seen by the next walk.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass

from . import persistence
from .settings import settings

logger = logging.getLogger(__name__)

#: How old a measure may grow before a storage limit waits for the next one.
MEASURE_SECONDS = 15 * 60
_URGENT, _BACKGROUND = 0, 1


@dataclass
class _Walk:
    """A walk of one user's storage, asked for or under way."""

    future: asyncio.Future[int]
    loop: asyncio.AbstractEventLoop
    started: bool = False
    #: Bytes stored while the walk ran, which it may have passed by.
    stored: int = 0
    #: Whether its measure is kept: not when the storage changed in bulk under it.
    keep: bool = True


# Per user: when the walk behind the measure started, and the bytes it found plus those stored since.
_measures: dict[str, tuple[float, int]] = {}
_walks: dict[str, _Walk] = {}
_queue: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
_order = itertools.count()
_worker: threading.Thread | None = None
_loaded = False


def _path() -> str:
    """Return where this node keeps its measures."""
    return os.path.join(settings.node_state_path, "storage_usage.yml")


def _load() -> None:
    """Take up the measures this node saved, once."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    saved = persistence.read_yaml(_path(), {}) or {}
    for username, (started, total) in saved.items():
        _measures.setdefault(str(username), (float(started), int(total)))


def _save() -> None:
    """Write the measures down, so that a restart keeps them."""
    try:
        os.makedirs(settings.node_state_path, exist_ok=True, mode=0o700)
        persistence.write_yaml_sync(_path(), {name: [started, total] for name, (started, total) in _measures.items()})
    except OSError as exc:
        logger.warning("Could not save the storage measures: %s", exc)


def _walk(username: str) -> tuple[float, int]:
    """Add up the bytes of every file under a user's storage directory."""
    started = time.time()
    total = 0
    for directory, _subdirs, files in os.walk(os.path.join(settings.storage_path, username)):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                continue
    return started, total


def _work() -> None:
    """Walk what is asked for, most wanted first, one walk at a time."""
    while True:
        _priority, _n, username = _queue.get()
        walk = _walks.get(username)
        # The copy an entry left behind when it went ahead of the queue, or a walk nobody wants now.
        if walk is None or walk.started:
            continue
        walk.started = True
        try:
            result: tuple[float, int] | Exception = _walk(username)
        except Exception as exc:  # noqa: BLE001 - handed to whoever waits on the walk
            result = exc
        try:
            walk.loop.call_soon_threadsafe(_finish, username, walk, result)
        except RuntimeError:
            continue  # the loop that asked is closed


def _finish(username: str, walk: _Walk, result: tuple[float, int] | Exception) -> None:
    """Keep a finished walk's measure, unless the storage changed in bulk under it, and answer those who wait."""
    if _walks.get(username) is walk:
        del _walks[username]
    if isinstance(result, Exception):
        if not walk.future.done():
            walk.future.set_exception(result)
        return
    started, total = result
    total += walk.stored
    if walk.keep:
        _measures[username] = (started, total)
        _save()
    if not walk.future.done():
        walk.future.set_result(total)


def _measure(username: str, urgent: bool) -> asyncio.Future[int]:
    """Ask for a walk of a user's storage, or join the one asked for already."""
    global _worker
    walk = _walks.get(username)
    if walk is None:
        loop = asyncio.get_running_loop()
        walk = _Walk(loop.create_future(), loop)
        _walks[username] = walk
    elif not urgent or walk.started:
        return walk.future
    # A walk somebody waits on is queued again, ahead; the worker passes over the copy it leaves behind.
    _queue.put((_URGENT if urgent else _BACKGROUND, next(_order), username))
    if _worker is None or not _worker.is_alive():
        # A daemon: a walk only reads, so the server never waits for one to end before it stops.
        _worker = threading.Thread(target=_work, name="storage-walk", daemon=True)
        _worker.start()
    return walk.future


async def used(username: str, *, current: bool = False) -> int:
    """Return the bytes under a user's storage directory on this node.

    Args:
        username: The user, whose directory is named after them.
        current: Wait for a walk when the measure is older than
            `MEASURE_SECONDS`, as a storage limit does; otherwise such a
            measure is answered with while the next walk runs.
    """
    _load()
    measure = _measures.get(username)
    if measure and time.time() - measure[0] < MEASURE_SECONDS:
        return measure[1]
    waits = current or measure is None
    future = _measure(username, urgent=waits)
    if measure and not waits:
        return measure[1]
    # Shielded, so a request given up on does not end a walk that others wait for.
    return await asyncio.shield(future)


def _owner(path: str) -> str:
    """Return the name of the storage directory that holds `path`, or an empty string for none."""
    root = os.path.realpath(settings.storage_path)
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        return ""
    return real[len(root) + 1 :].split(os.sep, 1)[0]


def stored(path: str, size: int) -> None:
    """Count `size` bytes the server placed at or under `path` in the measure of the storage that holds it."""
    owner = _owner(path)
    walk = _walks.get(owner)
    if walk and walk.started:
        walk.stored += size
    measure = _measures.get(owner)
    if measure:
        _measures[owner] = (measure[0], measure[1] + size)
        _save()


def changed(path: str) -> None:
    """Drop the measure of the storage that holds `path`, and a walk already past it, after files there change in bulk."""
    owner = _owner(path)
    if _measures.pop(owner, None) is not None:
        _save()
    walk = _walks.get(owner)
    if walk and walk.started:
        # Those who wait on it still get its answer; the next ask walks again.
        walk.keep = False
        del _walks[owner]
