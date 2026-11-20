"""How a user's storage is measured: rarely, one walk at a time, and kept current between walks."""

import asyncio
import threading

import pytest

from app import storage_usage
from app.settings import settings


@pytest.fixture
def walks(tmp_path, monkeypatch):
    """Users `ann`, `bob`, and `cy`, ten bytes each in their storage, and the list of the walks made of it."""
    monkeypatch.setattr(settings, "storage_path", str(tmp_path / "storage"))
    for name in ("ann", "bob", "cy"):
        (tmp_path / "storage" / name).mkdir(parents=True)
        (tmp_path / "storage" / name / "a").write_bytes(b"x" * 10)
    made = []
    walk = storage_usage._walk
    monkeypatch.setattr(storage_usage, "_walk", lambda name: made.append(name) or walk(name))
    return made


def _age(name, seconds):
    started, total = storage_usage._measures[name]
    storage_usage._measures[name] = (started - seconds, total)


@pytest.mark.asyncio
async def test_a_measure_is_kept_and_only_a_storage_limit_waits_for_a_stale_one(walks):
    home = settings.storage_path + "/ann"
    assert await asyncio.gather(storage_usage.used("ann"), storage_usage.used("ann")) == [10, 10]
    with open(home + "/b", "wb") as handle:
        handle.write(b"x" * 5)
    assert await storage_usage.used("ann", current=True) == 10 and walks == ["ann"]

    # Stale, the old measure answers the profile at once and the next one lands behind it.
    _age("ann", storage_usage.MEASURE_SECONDS + 1)
    assert await storage_usage.used("ann") == 10
    await storage_usage._walks["ann"].future
    assert await storage_usage.used("ann") == 15 and walks == ["ann", "ann"]

    # A storage limit waits for a walk rather than judge on a stale measure.
    with open(home + "/c", "wb") as handle:
        handle.write(b"x" * 5)
    _age("ann", storage_usage.MEASURE_SECONDS + 1)
    assert await storage_usage.used("ann", current=True) == 20 and walks == ["ann", "ann", "ann"]


@pytest.mark.asyncio
async def test_what_the_server_stores_counts_at_once_and_a_bulk_change_measures_again(walks):
    home = settings.storage_path + "/ann"
    assert await storage_usage.used("ann") == 10
    with open(home + "/upload", "wb") as handle:
        handle.write(b"x" * 7)
    storage_usage.stored(home, 7)
    # Another user's storage, or none, is left alone.
    storage_usage.stored(settings.storage_path + "/bob", 100)
    storage_usage.stored("/elsewhere", 100)
    assert await storage_usage.used("ann", current=True) == 17 and walks == ["ann"]

    storage_usage.changed(home + "/upload")
    assert await storage_usage.used("ann", current=True) == 17 and walks == ["ann", "ann"]


@pytest.mark.asyncio
async def test_measures_outlive_a_restart(walks):
    assert await storage_usage.used("ann") == 10
    storage_usage._measures.clear()
    storage_usage._loaded = False
    assert await storage_usage.used("ann", current=True) == 10 and walks == ["ann"]


@pytest.mark.asyncio
async def test_a_walk_somebody_waits_on_goes_before_those_in_the_background(walks, monkeypatch):
    release = threading.Event()
    walk = storage_usage._walk
    monkeypatch.setattr(storage_usage, "_walk", lambda name: (name != "ann" or release.wait(5)) and walk(name))
    first = asyncio.ensure_future(storage_usage.used("ann"))
    await asyncio.sleep(0.05)
    # Bob's stale measure is refreshed in the background; Cy's limit waits for one.
    storage_usage._measures["bob"] = (0.0, 10)
    assert await storage_usage.used("bob") == 10
    behind = storage_usage._walks["bob"].future
    waiting = asyncio.ensure_future(storage_usage.used("cy", current=True))
    await asyncio.sleep(0.01)
    release.set()
    assert await first == 10 and await waiting == 10 and await behind == 10
    assert walks == ["ann", "cy", "bob"]
