"""Limits on what a user may run and the record of what they ran.

* **Session limit.** `session_limit` counts a user's sessions on every node,
  as the nodes last reported them; a node that is away counts with what it
  last said, so losing sight of a node never frees a slot.
* **Session size.** `session_cpus` and `session_memory_mb` cap each session's
  container, below whatever the app or its template asks for.
* **Session length.** `session_hours` ends a session that old.
* **Allowance.** `allowance_hours` per `allowance_period` (`day`, `week`, or
  `month`, in UTC) is spent by session time, weighted by the `cost` of the
  pool the session runs in (`gpu_cost` for a GPU session), so an hour on a
  GPU node can cost more than an hour of office work. A user out of allowance
  starts nothing new; a pool with `stop_when_spent` also ends what runs.
* **Storage.** `storage_limit`, in gigabytes, stops new uploads and persistent
  launches once the user's directories on the node hold that much.

Each node adds up the time of its own sessions and writes it to
`cluster/usage/<day>/<node id>.yml` every `usage_flush_seconds`, an object no
other node writes. What is spent is the sum over the nodes, plus this node's
time not yet written.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import time
from typing import Any

from fastapi import HTTPException

from . import cluster, persistence
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

#: Weighted seconds per day, node, and user, as read from the store.
USAGE: dict[str, dict[str, dict[str, float]]] = {}
_own: dict[str, dict[str, float]] = {}
_dirty: set[str] = set()
_last_tick = 0.0
_storage_cache: dict[str, tuple[float, int]] = {}

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)b?\s*$", re.IGNORECASE)


def _today() -> str:
    """Return today's date in UTC."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


def _usage_dir(day: str = "") -> str:
    """Return the directory of the usage records, or of one day's."""
    return os.path.join(settings.cluster_path, "usage", day) if day else os.path.join(settings.cluster_path, "usage")


def period_days(period: str) -> list[str]:
    """Return the dates, in UTC, of the day, week (from Monday), or month running now."""
    today = datetime.datetime.now(datetime.UTC).date()
    if period == "day":
        first = today
    elif period == "week":
        first = today - datetime.timedelta(days=today.weekday())
    else:
        first = today.replace(day=1)
    return [(first + datetime.timedelta(days=n)).isoformat() for n in range((today - first).days + 1)]


def load() -> None:
    """Read the usage records of the running month; a day gone by is read once."""
    today = _today()
    wanted = set(period_days("month")) | set(period_days("week"))
    for day in list(USAGE):
        if day not in wanted:
            del USAGE[day]
    for day in sorted(wanted):
        if day in USAGE and day != today:
            continue
        nodes: dict[str, dict[str, float]] = {}
        for name in persistence.list_names(_usage_dir(day)):
            try:
                record = persistence.read_yaml(os.path.join(_usage_dir(day), name), {}) or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not read usage record %s/%s: %s", day, name, exc)
                continue
            nodes[name.removesuffix(".yml")] = {str(u): float(v) for u, v in record.items()}
        USAGE[day] = nodes
    if not _own:
        _own[today] = dict(USAGE.get(today, {}).get(cluster.NODE_ID, {}))


def tick() -> None:
    """Add the time since the last tick to every running session's user."""
    global _last_tick
    now = time.time()
    elapsed = min(now - _last_tick, 300) if _last_tick else 0.0
    _last_tick = now
    if elapsed <= 0 or not state.sessions:
        return
    pool = cluster.pool_record(cluster.pool_of(cluster.NODE_ID))
    cost = float(pool.get("cost") or 1.0)
    gpu_cost = float(pool.get("gpu_cost") or cost)
    day = _today()
    totals = _own.setdefault(day, dict(USAGE.get(day, {}).get(cluster.NODE_ID, {})))
    for data in state.sessions.values():
        username = data.get("username")
        if username:
            totals[username] = totals.get(username, 0.0) + elapsed * (gpu_cost if data.get("gpu_config") else cost)
    _dirty.add(day)


async def flush() -> None:
    """Write this node's usage of the days it changed."""
    for day in sorted(_dirty):
        record = {user: round(seconds, 1) for user, seconds in sorted(_own.get(day, {}).items())}
        path = os.path.join(_usage_dir(day), f"{cluster.NODE_ID}.yml")
        try:
            await asyncio.to_thread(persistence.read_bytes, path)
            await asyncio.to_thread(persistence.write_yaml_sync, path, record)
        except Exception as exc:  # noqa: BLE001 - keep the time for the next flush
            logger.warning("Could not write usage for %s: %s", day, exc)
            continue
        _dirty.discard(day)
        USAGE.setdefault(day, {})[cluster.NODE_ID] = dict(record)
    for day in [d for d in _own if d != _today() and d not in _dirty]:
        del _own[day]


def used_hours(username: str, period: str) -> float:
    """Return the weighted hours a user spent in the running period, on every node."""
    seconds = 0.0
    for day in period_days(period):
        for node_id, users in USAGE.get(day, {}).items():
            if node_id != cluster.NODE_ID or day not in _own:
                seconds += users.get(username, 0.0)
        seconds += _own.get(day, {}).get(username, 0.0)
    return seconds / 3600


def allowance(user: dict[str, Any]) -> dict[str, Any] | None:
    """Return a user's allowance and what is spent of it, or `None` for a user with none."""
    effective = user.get("effective_settings") or {}
    hours = effective.get("allowance_hours", -1)
    if user.get("is_admin") or hours is None or hours < 0:
        return None
    period = effective.get("allowance_period") or "month"
    return {"hours": hours, "period": period, "used": round(used_hours(user["username"], period), 2)}


async def check_launch(user: dict[str, Any]) -> None:
    """Refuse a launch that would take a user past the session limit or the allowance.

    The other nodes are asked for their sessions first, so the count is as
    fresh as they can make it.

    Raises:
        HTTPException: 403 with the limit that was reached.
    """
    if user.get("is_admin"):
        return
    effective = user.get("effective_settings") or {}
    limit = effective.get("session_limit", -1)
    if limit is not None and limit >= 0:
        if cluster.is_clustered():
            await cluster.refresh_peers(timeout=3)
        running = len(cluster.sessions_of(user["username"]))
        if running >= limit:
            raise HTTPException(
                status_code=403,
                detail=f"Session limit reached: {running} of {limit} session(s) are running. Stop one to start another.",
            )
    spent = allowance(user)
    if spent and spent["used"] >= spent["hours"]:
        raise HTTPException(
            status_code=403,
            detail=f"The allowance of {spent['hours']} hour(s) per {spent['period']} is spent.",
        )


def _bytes(value: Any) -> int | None:
    """Parse a Docker memory size (`512m`, `4g`, a byte count) into bytes."""
    if isinstance(value, int | float):
        return int(value)
    match = _SIZE.match(str(value or ""))
    if not match:
        return None
    scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[match.group(2).lower()]
    return int(float(match.group(1)) * scale)


def cap_resources(overrides: dict[str, Any], effective_settings: dict[str, Any] | None) -> None:
    """Lower a launch's CPU and memory to the user's `session_cpus` and `session_memory_mb`.

    Args:
        overrides: Docker run options of the launch, changed in place.
        effective_settings: The user's effective settings.
    """
    effective = effective_settings or {}
    cpus = effective.get("session_cpus", -1)
    if cpus is not None and cpus > 0:
        cap = int(float(cpus) * 1_000_000_000)
        overrides["nano_cpus"] = min(int(overrides.get("nano_cpus") or cap), cap)
    memory = effective.get("session_memory_mb", -1)
    if memory is not None and memory > 0:
        cap = int(memory) * 1024 * 1024
        asked = _bytes(overrides.get("mem_limit"))
        overrides["mem_limit"] = min(asked, cap) if asked else cap


def storage_used(username: str) -> int:
    """Return the bytes under a user's storage directory on this node, measured at most once a minute."""
    cached = _storage_cache.get(username)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    total = 0
    for directory, _subdirs, files in os.walk(os.path.join(settings.storage_path, username)):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                continue
    _storage_cache[username] = (time.time(), total)
    return total


async def check_storage(user: dict[str, Any]) -> None:
    """Refuse to add to the storage of a user who is at their `storage_limit`.

    Raises:
        HTTPException: 403 when the user's directories hold the limit or more.
    """
    limit = (user.get("effective_settings") or {}).get("storage_limit", -1)
    if user.get("is_admin") or limit is None or limit < 0:
        return
    used = await asyncio.to_thread(storage_used, user["username"])
    if used >= limit * 1024**3:
        raise HTTPException(
            status_code=403,
            detail=f"Storage limit reached: {used / 1024**3:.1f} of {limit} GB used. Delete files to continue.",
        )


def overdue() -> list[tuple[str, str]]:
    """Return the sessions on this node to end now, each with the reason.

    That is a session older than its user's `session_hours`, and, in a pool
    with `stop_when_spent`, one whose user is out of allowance.
    """
    from . import user_manager

    ending: list[tuple[str, str]] = []
    pool = cluster.pool_record(cluster.pool_of(cluster.NODE_ID))
    now = time.time()
    for session_id, data in state.sessions.items():
        username = data.get("username") or ""
        record = user_manager.get_user(username)
        if not record or record.get("is_admin"):
            continue
        effective = user_manager.get_effective_settings(username, data.get("provider_groups") or ())
        if effective.get("admin"):
            continue
        hours = effective.get("session_hours", -1)
        if hours is not None and hours >= 0 and now - float(data.get("created_at") or now) >= hours * 3600:
            ending.append((session_id, f"it reached the session length of {hours} hour(s)"))
            continue
        if pool.get("stop_when_spent"):
            spent = allowance({"username": username, "effective_settings": effective})
            if spent and spent["used"] >= spent["hours"]:
                ending.append((session_id, "its user's allowance is spent"))
    return ending
