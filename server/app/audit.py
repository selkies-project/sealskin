"""An append-only record of who did what on this node.

Each event is one JSON line in `<node_state_path>/audit/<day>.log`, in UTC:
sign-ins and refusals, launches and stops, and every administrative change
with the administrator who made it. A node keeps its own log; the dashboard
reads the logs of all nodes together.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from typing import Any

from .settings import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _directory() -> str:
    """Return the directory of the audit logs."""
    return os.path.join(settings.node_state_path, "audit")


def record(event: str, user: str = "", **fields: Any) -> None:
    """Append an event; a log that cannot be written never fails the request that caused it.

    Args:
        event: What happened, `launch` or `sign_in` for example.
        user: Who did it, when someone did.
        **fields: What it was done to.
    """
    now = datetime.datetime.now(datetime.UTC)
    entry = {"time": now.isoformat(timespec="milliseconds"), "event": event, "user": user}
    entry.update({key: value for key, value in fields.items() if value not in (None, "")})
    try:
        with _lock:
            os.makedirs(_directory(), exist_ok=True, mode=0o700)
            path = os.path.join(_directory(), f"{now:%Y-%m-%d}.log")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        logger.warning("Could not write the audit log: %s", exc)


def days() -> list[str]:
    """Return the days this node has a log for, newest first."""
    try:
        names = os.listdir(_directory())
    except OSError:
        return []
    return sorted((name[:-4] for name in names if name.endswith(".log")), reverse=True)


def read(day: str = "", query: str = "", last: str = "") -> list[dict[str, Any]]:
    """Return the events of a day, oldest first.

    Args:
        day: `YYYY-MM-DD` in UTC; today when empty.
        query: Keep the events whose line contains every word of this, in any case.
        last: With `day` empty, also read the days before today back to this one.
    """
    today = datetime.datetime.now(datetime.UTC).date()
    try:
        first = datetime.date.fromisoformat(day) if day else today
        until = first if day or not last else first
        since = first if day or not last else datetime.date.fromisoformat(last)
    except ValueError:
        return []
    words = [word for word in query.lower().split() if word]
    events = []
    current = min(since, until)
    while current <= max(since, until):
        try:
            with open(os.path.join(_directory(), f"{current.isoformat()}.log"), encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            lines = []
        for line in lines:
            lowered = line.lower()
            if any(word not in lowered for word in words):
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        current += datetime.timedelta(days=1)
    return events
