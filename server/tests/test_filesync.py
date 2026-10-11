"""Shared files: a launch fetches the one file it opens, the rest arrives behind it, and files land whole."""

import os

import pytest

from app import cluster, filesync, store
from app.settings import settings

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "files_sync", "on")
    monkeypatch.setattr(cluster, "NODE_ID", "abcdef0123456789")
    root = tmp_path / "storage" / "alice" / "_sealskin_shared_files"
    store.put("files/alice/report.pdf", b"report")
    store.put("files/alice/notes/todo.txt", b"todo")
    return str(root)


@pytest.mark.asyncio
async def test_one_file_is_fetched_on_its_own_and_a_pull_brings_the_rest(shared):
    assert await filesync.pull_file("alice", shared, "report.pdf") is True
    assert open(os.path.join(shared, "report.pdf"), "rb").read() == b"report"
    assert not os.path.exists(os.path.join(shared, "notes", "todo.txt"))
    assert not [n for n in os.listdir(shared) if ".partial-" in n]
    assert await filesync.pull_file("alice", shared, "missing.txt") is False
    assert await filesync.pull_file("alice", shared, "../../etc/passwd") is False
    await filesync.pull("alice", shared)
    assert open(os.path.join(shared, "notes", "todo.txt"), "rb").read() == b"todo"
    # A file current on this node is not fetched again, and a changed one is.
    store.put("files/alice/report.pdf", b"report v2")
    assert await filesync.pull_file("alice", shared, "report.pdf") is True
    assert open(os.path.join(shared, "report.pdf"), "rb").read() == b"report v2"


@pytest.mark.asyncio
async def test_sync_is_off_with_one_node_and_the_pull_looks_at_the_disk_alone(shared, monkeypatch):
    monkeypatch.setattr(settings, "files_sync", "auto")
    assert filesync.enabled() is False
    assert await filesync.pull_file("alice", shared, "report.pdf") is False
