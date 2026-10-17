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


async def test_a_server_takes_only_the_container_with_its_own_host_name_for_itself(monkeypatch):
    import os

    from docker.errors import NotFound

    from app import docker_utils

    class Container:
        def __init__(self, name, hostname):
            self.name, self.id = name, "f" * 64
            self.attrs = {"Config": {"Hostname": hostname}, "Mounts": [{"Source": "/host", "Destination": "/config"}]}

    class Containers:
        def __init__(self, known):
            self.known = known

        def get(self, name):
            if name not in self.known:
                raise NotFound(name)
            return self.known[name]

    class Client:
        def __init__(self, known):
            self.containers = Containers(known)

    monkeypatch.setattr(os.path, "exists", lambda path: True)
    here = os.uname()[1]
    monkeypatch.setattr(docker_utils, "get_docker_client", lambda: Client({"sealskin": Container("sealskin", "another-host")}))
    await docker_utils.inspect_self_container()
    assert state.instance_name == here and not state.path_prefix_map
    monkeypatch.setattr(docker_utils, "get_docker_client", lambda: Client({"sealskin": Container("sealskin", here)}))
    await docker_utils.inspect_self_container()
    assert state.instance_name == "sealskin" and state.path_prefix_map == {"/config": "/host"}
