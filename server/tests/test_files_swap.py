"""The file manager acts on what it checked, however the tree changes after the check.

A user's session container mounts their home read-write, so between the moment a
path is resolved inside the home and the moment it is used, the user can put a
symlink in its place, or in place of a directory on the way, pointing into another
user's storage. Every operation (download, public share, upload, delete, new
folder, listing) is driven here with that swap made right after the path was
validated, and none of them may read, publish, write, remove, create, or list
anything of the other user's.
"""

import asyncio
import os
import shutil
import tempfile

import pytest
from fastapi import HTTPException

from app import fsutil, user_manager
from app.routers import files, shares
from app.settings import settings
from app.state import state


@pytest.fixture
def homes(monkeypatch):
    storage = settings.storage_path
    alice = os.path.join(storage, "alice", "home1")
    bob = os.path.join(storage, "bob", "home1")
    for root in (alice, bob):
        os.makedirs(os.path.join(root, "d"))
    with open(os.path.join(alice, "a.txt"), "w") as f:
        f.write("alice's")
    with open(os.path.join(alice, "d", "f.txt"), "w") as f:
        f.write("alice's")
    with open(os.path.join(bob, "secret.txt"), "w") as f:
        f.write("bob's secret")
    with open(os.path.join(bob, "d", "f.txt"), "w") as f:
        f.write("bob's file")
    with open(os.path.join(bob, "d", "bob-only.txt"), "w") as f:
        f.write("bob's file")
    monkeypatch.setattr(user_manager, "get_home_dirs", lambda username: ["home1"])
    os.makedirs(settings.public_storage_path, exist_ok=True)
    yield alice, bob
    shutil.rmtree(os.path.join(storage, "alice"), ignore_errors=True)
    shutil.rmtree(os.path.join(storage, "bob"), ignore_errors=True)


def swap_after_validation(monkeypatch, target: str, up: int = 0):
    """Once a path is validated, move it, or the directory `up` levels above it, aside
    and put a symlink to `target` in its place."""
    real = files.get_validated_path

    def validated_then_swapped(*args, **kwargs):
        path = real(*args, **kwargs)
        spot = str(path)
        for _ in range(up):
            spot = os.path.dirname(spot)
        os.rename(spot, f"{spot}.aside")
        os.symlink(target, spot)
        return path

    monkeypatch.setattr(files, "get_validated_path", validated_then_swapped)
    monkeypatch.setattr(shares, "get_validated_path", validated_then_swapped)


ALICE = {"username": "alice"}


def test_a_download_reads_no_other_users_file(homes, monkeypatch):
    alice, bob = homes
    swap_after_validation(monkeypatch, os.path.join(bob, "secret.txt"))
    with pytest.raises(HTTPException):
        asyncio.run(files.download_file_chunk("home1", path="/a.txt", chunk_index=0, user=ALICE))


def test_a_public_share_publishes_no_other_users_file(homes, monkeypatch):
    alice, bob = homes
    swap_after_validation(monkeypatch, os.path.join(bob, "secret.txt"))
    with pytest.raises(HTTPException):
        asyncio.run(shares.create_public_share({"home_dir": "home1", "path": "/a.txt"}, user=ALICE))
    for name in os.listdir(settings.public_storage_path):
        with open(os.path.join(settings.public_storage_path, name), "rb") as f:
            assert f.read() != b"bob's secret"


def test_an_upload_writes_nothing_into_another_users_storage(homes, monkeypatch):
    alice, bob = homes
    swap_after_validation(monkeypatch, os.path.join(bob, "d"))
    staged = tempfile.mkdtemp()

    async def reassembled(username, upload_id, total_chunks):
        path = os.path.join(staged, "upload")
        with open(path, "w") as f:
            f.write("alice's upload")
        return path

    monkeypatch.setattr(files, "reassemble_file", reassembled)
    with pytest.raises(HTTPException):
        asyncio.run(files.finalize_upload_to_dir(
            "home1", {"path": "/d", "filename": "x.txt", "upload_id": "u", "total_chunks": 1}, user=ALICE
        ))
    assert "x.txt" not in os.listdir(os.path.join(bob, "d"))


def test_a_download_through_a_swapped_directory_reads_no_other_users_file(homes, monkeypatch):
    alice, bob = homes
    swap_after_validation(monkeypatch, os.path.join(bob, "d"), up=1)
    with pytest.raises(HTTPException):
        asyncio.run(files.download_file_chunk("home1", path="/d/f.txt", chunk_index=0, user=ALICE))


def test_a_delete_removes_nothing_of_another_users(homes, monkeypatch):
    alice, bob = homes
    swap_after_validation(monkeypatch, os.path.join(bob, "d"), up=1)
    state.deletion_tasks["t"] = {"status": "pending", "username": "alice"}
    asyncio.run(files._perform_deletion("t", "alice", "home1", ["/d/f.txt"]))
    assert os.path.exists(os.path.join(bob, "d", "f.txt"))
    state.deletion_tasks.pop("t", None)


def test_a_new_folder_lands_in_no_other_users_tree(homes, monkeypatch):
    alice, bob = homes
    swap_after_validation(monkeypatch, os.path.join(bob, "d"))
    with pytest.raises(HTTPException):
        asyncio.run(files.create_folder("home1", {"path": "/d", "folder_name": "new"}, user=ALICE))
    assert not os.path.exists(os.path.join(bob, "d", "new"))


def test_a_listing_shows_nothing_of_another_users(homes, monkeypatch):
    alice, bob = homes
    swap_after_validation(monkeypatch, os.path.join(bob, "d"))
    try:
        listed = asyncio.run(files.list_files("home1", path="/d", page=1, per_page=50, user=ALICE))
        names = [item["name"] for item in listed["items"]]
    except HTTPException:
        names = []
    assert "bob-only.txt" not in names


def test_unswapped_operations_still_work(homes):
    alice, bob = homes
    chunk = asyncio.run(files.download_file_chunk("home1", path="/a.txt", chunk_index=0, user=ALICE))
    assert chunk["is_last_chunk"] and chunk["chunk_data_b64"]
    asyncio.run(files.create_folder("home1", {"path": "/d", "folder_name": "new"}, user=ALICE))
    assert os.path.isdir(os.path.join(alice, "d", "new"))
    listed = asyncio.run(files.list_files("home1", path="/d", page=1, per_page=50, user=ALICE))
    assert {item["name"] for item in listed["items"]} == {"f.txt", "new"}
    assert {item["path"] for item in listed["items"]} == {"/d/f.txt", "/d/new"}
    staged = tempfile.mkdtemp()
    for _ in range(2):
        path = os.path.join(staged, "upload")
        with open(path, "w") as f:
            f.write("up")

        async def reassembled(username, upload_id, total_chunks, path=path):
            return path

        files.reassemble_file, saved = reassembled, files.reassemble_file
        try:
            asyncio.run(files.finalize_upload_to_dir(
                "home1", {"path": "/d", "filename": "up.txt", "upload_id": "u", "total_chunks": 1}, user=ALICE
            ))
        finally:
            files.reassemble_file = saved
    assert {"up.txt", "up-1.txt"} <= set(os.listdir(os.path.join(alice, "d")))
    state.deletion_tasks["t"] = {"status": "pending", "username": "alice"}
    asyncio.run(files._perform_deletion("t", "alice", "home1", ["/d/new", "/d/up.txt"]))
    assert state.deletion_tasks.pop("t")["status"] == "completed"
    assert not os.path.exists(os.path.join(alice, "d", "new"))
    assert not os.path.exists(os.path.join(alice, "d", "up.txt"))


def test_a_file_created_by_name_passes_over_a_planted_symlink(tmp_path):
    """A launch's file lands under its name or the next free one, never where a symlink
    already holding the name points, dangling or not."""
    folder, outside = tmp_path / "files", tmp_path / "outside"
    folder.mkdir()
    outside.mkdir()
    (folder / "doc.txt").symlink_to(outside / "planted.txt")
    dir_fd = fsutil.open_within(str(folder), str(folder), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fd, name = fsutil.create_exclusive(dir_fd, "doc.txt")
        os.close(fd)
    finally:
        os.close(dir_fd)
    assert name == "doc-1.txt"
    assert not (outside / "planted.txt").exists()


def test_an_upload_placed_by_name_passes_over_a_planted_symlink(tmp_path):
    """An upload moved into a folder its user's sessions write lands under a free name,
    never where a symlink already holding its name points."""
    folder, outside, staged = tmp_path / "shared", tmp_path / "outside", tmp_path / "upload"
    folder.mkdir()
    outside.mkdir()
    staged.write_text("upload")
    (folder / "x.txt").symlink_to(outside / "planted.txt")
    name = fsutil.place_file(str(staged), str(folder), str(folder), "x.txt")
    assert name == "x-1.txt" and (folder / "x-1.txt").read_text() == "upload"
    assert not (outside / "planted.txt").exists() and not staged.exists()
