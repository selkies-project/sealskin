"""Session reconciliation against a backend's view of its instances."""

import asyncio
import os
import time

from app import launch
from app.state import state


class FakeProvider:
    orphan_grace = 100

    def __init__(self, running, owned, fail=False):
        self.running = set(running)
        self.owned = owned
        self.fail = fail
        self.stopped = []

    async def managed_instances(self):
        if self.fail:
            raise RuntimeError("backend unreachable")
        return self.owned

    async def is_running(self, instance_id):
        return instance_id in self.running

    async def stop(self, instance_id):
        self.stopped.append(instance_id)


def _use(monkeypatch, provider):
    monkeypatch.setattr(launch, "get_provider", lambda *args: provider)


async def test_ended_sessions_stop_and_orphans_go(monkeypatch):
    ephemeral = launch.new_ephemeral_dir()
    old = time.time() - 1000
    state.sessions.update(
        {
            "live": {
                "instance_id": "a",
                "container_registry": {"app1": {"instance_id": "a"}, "app2": {"instance_id": "b"}},
            },
            "dead": {
                "instance_id": "c",
                "provider_app_id": "app1",
                "container_registry": {"app1": {"instance_id": "c"}},
                "host_mount_path": ephemeral,
            },
        }
    )
    provider = FakeProvider(running={"a"}, owned={"a": old, "b": old, "stray": old, "starting": time.time()})
    _use(monkeypatch, provider)

    await launch.reconcile_sessions()
    await asyncio.gather(*launch._removals)

    assert set(state.sessions) == {"live"}
    assert state.sessions["live"]["container_registry"] == {"app1": {"instance_id": "a"}}
    assert not os.path.exists(ephemeral)
    assert provider.stopped == ["c", "stray"]


async def test_backend_outage_changes_nothing(monkeypatch):
    state.sessions["s"] = {"instance_id": "gone"}
    provider = FakeProvider(running=set(), owned={}, fail=True)
    _use(monkeypatch, provider)
    await launch.reconcile_sessions()
    assert "s" in state.sessions and provider.stopped == []
