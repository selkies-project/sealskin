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


@pytest.mark.asyncio
async def test_files_are_encrypted_under_a_key_of_the_users_own_and_shredded_with_the_user(shared, monkeypatch, tmp_path):
    from app import filecrypt, persistence

    monkeypatch.setattr(settings, "store_key", "a-long-random-string-every-node-holds")
    filecrypt.reset()
    os.makedirs(shared, exist_ok=True)
    with open(os.path.join(shared, "secret.txt"), "wb") as handle:
        handle.write(b"the content")
    await filesync.sync("alice", shared)
    sealed = store.get("files/alice/secret.txt")[0]
    assert sealed.startswith(filecrypt.MAGIC) and b"the content" not in sealed
    wrapped = persistence.read_yaml(filecrypt._path("alice"))
    assert wrapped["key_id"] == filecrypt.key_id() and filecrypt.user_key("alice") != filecrypt.user_key("bob")
    # Another node (its own manifest) reads them, and a file written before the key was set as it is.
    elsewhere = str(tmp_path / "other" / "_sealskin_shared_files")
    os.remove(filesync._manifest_path("alice"))
    await filesync.pull("alice", elsewhere)
    assert open(os.path.join(elsewhere, "secret.txt"), "rb").read() == b"the content"
    assert open(os.path.join(elsewhere, "report.pdf"), "rb").read() == b"report"
    # Under another cluster key the file is left alone on both sides.
    monkeypatch.setattr(settings, "store_key", "another-key")
    filecrypt.reset()
    third = str(tmp_path / "third" / "_sealskin_shared_files")
    os.remove(filesync._manifest_path("alice"))
    await filesync.pull("alice", third)
    assert not os.path.exists(os.path.join(third, "secret.txt"))
    assert open(os.path.join(third, "report.pdf"), "rb").read() == b"report"
    monkeypatch.setattr(settings, "store_key", "a-long-random-string-every-node-holds")
    filecrypt.reset()
    filesync.forget("alice")
    assert store.list_keys("files/alice/") == {} and persistence.read_yaml(filecrypt._path("alice")) is None


@pytest.mark.asyncio
async def test_a_pull_never_writes_through_a_link_a_session_left_in_the_directory(shared, tmp_path, caplog):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.makedirs(shared)
    os.symlink(str(outside), os.path.join(shared, "link"))
    os.symlink(str(outside / "target.txt"), os.path.join(shared, "linked.txt"))
    os.symlink(str(outside / "partial.txt"), os.path.join(shared, f"planted.txt.partial-{cluster.NODE_ID}"))
    store.put("files/alice/link/planted.txt", b"owned")
    store.put("files/alice/linked.txt", b"owned")
    store.put("files/alice/planted.txt", b"fine")
    await filesync.pull("alice", shared)
    assert os.listdir(outside) == [] and os.path.islink(os.path.join(shared, "linked.txt"))
    assert open(os.path.join(shared, "planted.txt"), "rb").read() == b"fine"
    assert sum("were not placed" in r.message for r in caplog.records) == 1
    assert await filesync.pull_file("alice", shared, "link/planted.txt") is False
    assert await filesync.pull_file("alice", shared, "linked.txt") is False
    assert os.listdir(outside) == []
    # What the directory holds is still sent, links aside.
    await filesync.sync("alice", shared)
    assert "files/alice/planted.txt" in store.list_keys("files/alice/") and "files/alice/linked.txt" in store.list_keys("files/alice/")
