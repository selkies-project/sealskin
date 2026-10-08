"""The web client taken out of an image once and served by the node."""

import io
import os
import tarfile

import pytest
from fastapi import HTTPException

from app import webclient
from app.settings import settings


def archive(files: dict[str, str], mode: str = "w:gz") -> bytes:
    """A tar of `files`, as the throwaway instance writes one."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class FakeProvider:
    """Answers the image questions the export asks."""

    def __init__(self, data: bytes, digest: str | None = "sha256:abc123"):
        self.data = data
        self.digest = digest
        self.exports = 0
        self.pulls = 0

    async def get_local_image_info(self, image):
        return {"id": self.digest, "short_id": "abc123", "digests": []} if self.digest else None

    async def pull_image(self, image):
        self.pulls += 1
        self.digest = self.digest or "sha256:pulled"

    async def export_web_client(self, image, path):
        self.exports += 1
        return self.data


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider(archive({"./selkies-dashboard/index.html": "<html>", "./selkies-dashboard-wish/index.html": "<wish>"}))
    monkeypatch.setattr("app.providers.get_provider", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(webclient, "_locks", {})
    return fake


async def test_the_client_is_exported_once_per_image_and_served_by_dashboard(provider):
    bundle = await webclient.ensure("img:latest")
    assert bundle == os.path.join(settings.node_state_path, "web", "abc123")
    assert open(os.path.join(bundle, "selkies-dashboard", "index.html")).read() == "<html>"
    assert await webclient.ensure("img:latest") == bundle and provider.exports == 1
    assert webclient.dashboard_dir(bundle, {"DASHBOARD": "selkies-dashboard-wish"}).endswith("-wish")
    assert webclient.dashboard_dir(bundle, {}).endswith("selkies-dashboard")
    assert webclient.dashboard_dir(bundle, {"DASHBOARD": "missing"}).endswith("selkies-dashboard")
    root = await webclient.root_for_session("img:latest", {"DASHBOARD": "selkies-dashboard-wish"}, required=True)
    assert root == os.path.join(bundle, "selkies-dashboard-wish")


async def test_a_copy_of_the_directory_itself_is_taken_as_its_contents(provider):
    # Docker's copy API names the exported directory, uncompressed; the Kubernetes pod writes its contents.
    provider.data = archive({"selkies/selkies-dashboard/index.html": "<html>", "selkies/www/icon.png": "x"}, mode="w")
    bundle = await webclient.ensure("img:latest")
    assert sorted(os.listdir(bundle)) == ["selkies-dashboard", "www"]
    assert webclient.dashboard_dir(bundle, {}) == os.path.join(bundle, "selkies-dashboard")


async def test_an_image_without_a_digest_is_pulled_first(provider):
    provider.digest = None
    await webclient.ensure("img:latest")
    assert provider.pulls == 1 and os.path.isdir(webclient.bundle_dir("sha256:pulled"))


async def test_an_export_that_escapes_the_bundle_is_refused(provider):
    provider.data = archive({"../outside.html": "x", "./selkies-dashboard/index.html": "<html>"})
    with pytest.raises(RuntimeError, match="outside the bundle"):
        await webclient.ensure("img:latest")
    assert not os.path.exists(os.path.join(settings.node_state_path, "outside.html"))
    assert not os.path.isdir(webclient.bundle_dir("sha256:abc123"))


async def test_a_session_that_needs_the_client_fails_without_it(provider):
    provider.data = archive({"./readme.txt": "no client here"})
    with pytest.raises(HTTPException) as refused:
        await webclient.root_for_session("img:latest", {}, required=True)
    assert refused.value.status_code == 500 and "no web client" in refused.value.detail
    # A key-file session, or one on its own origin, falls back to the container's page.
    assert await webclient.root_for_session("img:latest", {}, required=False) is None


def test_prune_keeps_the_digests_in_use(tmp_path):
    root = webclient.bundles_root()
    for name in ("aaa", "bbb", ".export-tmp"):
        os.makedirs(os.path.join(root, name))
    webclient.prune({"sha256:aaa"})
    assert sorted(os.listdir(root)) == [".export-tmp", "aaa"]
