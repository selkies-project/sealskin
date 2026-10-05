"""Where a launch is, for the page that waits on it.

A client that wants to show progress names its launch (`launch_id`, a UUID it
makes up) in the launch request and asks `GET /api/launch/progress/<id>` while
the request runs. The launch reports each stage it reaches with `step`:

| Stage | The server is |
| --- | --- |
| `placing` | checking limits and choosing the node |
| `forwarding` | handing the launch to another node |
| `storage` | preparing the home directory |
| `files` | bringing the shared files up to date |
| `image` | pulling the application image |
| `starting` | creating the container |
| `waiting` | waiting for the desktop to answer |
| `ready` | done; the answer carries the session URL |
| `failed` | done; the answer carries the reason |

Closing an App Laboratory session reports `stopping` and `saving` the same
way, ending in `ready` with the template's `files` and `bytes`.

The launch in progress is carried in a context variable, so the code that
reaches a stage needs to know nothing of who asked. A frontend that forwarded
the launch asks the node that runs it.
"""

from __future__ import annotations

import contextvars
import re
import time
from typing import Any

#: Seconds a finished or abandoned launch is remembered.
KEEP_SECONDS = 900
MAX_RUNS = 5000

_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("launch_id", default=None)
_RUNS: dict[str, dict[str, Any]] = {}


def begin(launch_id: Any, username: str) -> str | None:
    """Start following the launch a request names and make it the current one.

    Args:
        launch_id: The id the client chose; anything but a UUID is ignored.
        username: The user launching, who alone may ask about it.

    Returns:
        The id when the launch is followed.
    """
    if not isinstance(launch_id, str) or not _ID.fullmatch(launch_id):
        _current.set(None)
        return None
    now = time.time()
    for stale in [k for k, run in _RUNS.items() if now - run["updated"] > KEEP_SECONDS]:
        del _RUNS[stale]
    known = _RUNS.get(launch_id)
    if known and known["user"] != username:
        _current.set(None)
        return None
    if not known and len(_RUNS) < MAX_RUNS:
        _RUNS[launch_id] = {"user": username, "stage": "placing", "detail": {}, "started": now, "updated": now}
    _current.set(launch_id if launch_id in _RUNS else None)
    return _current.get()


def step(stage: str, **detail: Any) -> None:
    """Note that the current launch reached `stage`; nothing when no launch is followed."""
    run = _RUNS.get(_current.get() or "")
    if run and run["stage"] not in ("ready", "failed"):
        run.update(stage=stage, detail={k: v for k, v in detail.items() if v not in (None, "")}, updated=time.time())


def forwarded(node_id: str, node_name: str) -> None:
    """Note that the current launch was handed to another node."""
    run = _RUNS.get(_current.get() or "")
    if run:
        run.update(stage="forwarding", detail={"node": node_name}, forwarded_to=node_id, updated=time.time())


def finish(result: dict[str, Any] | None = None, error: str = "", launch_id: str | None = None) -> None:
    """End a launch, the current one by default, with its answer or the reason it failed.

    A launch that ended already keeps its first ending.
    """
    run = _RUNS.get(launch_id or _current.get() or "")
    if not run or run["stage"] in ("ready", "failed"):
        return
    if error:
        run.update(stage="failed", error=error, updated=time.time())
    else:
        run.update(stage="ready", result=dict(result or {}), updated=time.time())


def get(launch_id: str, username: str) -> dict[str, Any] | None:
    """Return a launch's progress to the user who started it, or `None`."""
    run = _RUNS.get(launch_id)
    if not run or run["user"] != username:
        return None
    return run


def view(run: dict[str, Any]) -> dict[str, Any]:
    """Return what the client is told of a launch."""
    answer = {
        "stage": run["stage"],
        "detail": run.get("detail") or {},
        "elapsed": round(time.time() - run["started"], 1),
    }
    if run["stage"] == "ready":
        answer.update(run.get("result") or {})
    if run["stage"] == "failed":
        answer["error"] = run.get("error") or ""
    return answer
