"""Atomic YAML writes and per-path locks."""

import asyncio
import os

import pytest

from app import persistence


def test_write_yaml_sync_is_atomic_and_records_hash(tmp_path):
    path = tmp_path / "data.yml"
    persistence.write_yaml_sync(str(path), {"a": 1, "b": [1, 2]})
    assert persistence.read_yaml(str(path)) == {"a": 1, "b": [1, 2]}
    assert persistence.was_written_by_us(str(path))
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")]

    path.write_text("a: 2\n", encoding="utf-8")
    assert not persistence.was_written_by_us(str(path))


def test_read_yaml_default_for_missing_file(tmp_path):
    assert persistence.read_yaml(str(tmp_path / "missing.yml"), default=[]) == []


@pytest.mark.asyncio
async def test_write_yaml_serialises_concurrent_writers(tmp_path):
    path = str(tmp_path / "concurrent.yml")

    async def writer(value):
        await persistence.write_yaml(path, {"value": value})

    await asyncio.gather(*(writer(i) for i in range(10)))
    data = persistence.read_yaml(path)
    assert set(data) == {"value"} and data["value"] in range(10)
    assert persistence.lock_for(path) is persistence.lock_for(os.path.abspath(path))
