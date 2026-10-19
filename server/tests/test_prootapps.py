"""PRoot Apps catalogs: naming, the folder a sync writes, who gets which catalog, and the mount."""

import os

import httpx
import yaml

from app import launch, prootapps, user_manager
from app.models import InstalledApp, ProotCatalog
from app.settings import settings
from app.state import state


def test_names_follow_proot_apps():
    assert prootapps.image_folder("ghcr.io/linuxserver/proot-apps:firefox") == "ghcr.io_linuxserver_proot-apps_firefox"
    assert prootapps.remote_image("MyOrg/proot-apps", "gimp") == "ghcr.io/myorg/proot-apps:gimp"
    assert prootapps.install_name("linuxserver/proot-apps", "firefox") == "firefox"
    assert prootapps.install_name("myorg/proot-apps", "gimp") == "ghcr.io/myorg/proot-apps:gimp"
    assert prootapps.icon_file("myorg/proot-apps", "gimp.svg") == "ghcr.io_myorg_proot-apps_gimp.svg"
    assert prootapps.icon_url("MyOrg/proot-apps", "gimp.svg") == "https://raw.githubusercontent.com/myorg/proot-apps/master/metadata/img/gimp.svg"
    assert prootapps.is_remote("linuxserver/proot-apps") and not prootapps.is_remote("not a remote")


def test_a_remote_may_be_a_url_and_a_registry():
    remote = "  https://git.example/raw/IT/proot-apps/Main/   Registry.example:5000/it/proot-apps "
    parsed = prootapps.Remote.parse(remote)
    assert parsed.key == "https://git.example/raw/IT/proot-apps/Main registry.example:5000/it/proot-apps"
    assert parsed.metadata_url == "https://git.example/raw/IT/proot-apps/Main/metadata/metadata.yml"
    assert prootapps.icon_url(remote, "gimp.svg") == "https://git.example/raw/IT/proot-apps/Main/metadata/img/gimp.svg"
    assert prootapps.remote_image(remote, "gimp") == "registry.example:5000/it/proot-apps:gimp"
    assert prootapps.install_name(remote, "gimp") == "registry.example:5000/it/proot-apps:gimp"
    assert prootapps.icon_file(remote, "gimp.svg") == "registry.example_5000_it_proot-apps_gimp.svg"
    assert prootapps.image_folder(prootapps.remote_image(remote, "gimp")) == "registry.example_5000_it_proot-apps_gimp"
    assert not prootapps.is_remote("https://git.example/raw/it/proot-apps/main")
    assert not prootapps.is_remote("https://git.example/raw/it/proot-apps/main not a registry")
    assert not prootapps.is_remote("ftp://git.example/x registry.example/it/proot-apps")


def test_the_registry_client_signs_in_as_the_challenge_asks(monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.url.host, request.url.path, request.headers.get("Authorization")))
        if request.url.host == "bearer.example":
            if request.url.path == "/token":
                return httpx.Response(200, json={"token": "tok"})
            if request.headers.get("Authorization") != "Bearer tok":
                return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer realm="https://bearer.example/token",service="r",scope="repository:x/y:pull"'})
        if request.url.host == "basic.example" and request.headers.get("Authorization") != "Basic dTpw":
            return httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="Registry"'})
        return httpx.Response(200, json={"layers": [{"digest": "sha256:" + "c" * 64}]})

    auths = {"bearer.example": ("u", "p"), "basic.example": ("u", "p")}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert prootapps._Registry(client, "bearer.example/x/y:app", auths).layer_digest("amd64") == "sha256:" + "c" * 64
        assert prootapps._Registry(client, "basic.example/x/y:app", auths).layer_digest("amd64") == "sha256:" + "c" * 64
        assert prootapps._Registry(client, "bearer.example/x/y:app", {}).layer_digest("amd64") == "sha256:" + "c" * 64
    assert ("bearer.example", "/token", "Basic dTpw") in calls
    assert ("basic.example", "/v2/x/y/manifests/app", "Basic dTpw") in calls
    assert ("bearer.example", "/token", None) in calls


class FakeRegistry:
    """Stands in for the registry: one layer per tag, a known digest, a short package."""

    digests = {"firefox": "sha256:" + "a" * 64, "gimp": "sha256:" + "b" * 64}

    def __init__(self, _client, image, _auths=None):
        self.tag = image.rsplit(":", 1)[1]

    def layer_digest(self, _arch):
        return self.digests[self.tag]

    def download_blob(self, _digest, destination):
        with open(destination, "wb") as handle:
            handle.write(b"tar")
        return 3


def test_the_category_a_remote_lists_reaches_the_catalog_metadata():
    raw = b"""include:
  - name: gimp
    full_name: GIMP
    arch: linux/amd64
    category: Creative
    description: paint
  - name: vim
    full_name: Vim
    arch: linux/amd64
    description: edit
"""
    apps = prootapps._parse_metadata(raw, "linuxserver/proot-apps")
    assert [a["category"] for a in apps] == ["Creative", ""]
    folder = prootapps.catalog_dir("cat-categories")
    prootapps._write_metadata(folder, apps, None, {})
    with open(os.path.join(folder, "metadata", "metadata.yml")) as handle:
        include = yaml.safe_load(handle)["include"]
    assert include[0]["category"] == "Creative"
    assert "category" not in include[1]


def test_a_sync_writes_the_folder_proot_apps_reads(monkeypatch):
    monkeypatch.setattr(prootapps, "_Registry", FakeRegistry)
    monkeypatch.setattr(prootapps, "node_arch", lambda: "amd64")
    monkeypatch.setattr(prootapps, "_write_metadata", lambda folder, apps, client, status: _plain_metadata(folder, apps))
    catalog = ProotCatalog(
        id="cat1",
        name="Office",
        apps=[
            {"remote": "linuxserver/proot-apps", "name": "firefox"},
            {"remote": "myorg/proot-apps", "name": "gimp"},
            {"remote": "linuxserver/proot-apps", "name": "armonly"},
        ],
    )
    state.proot_catalogs[catalog.id] = catalog
    listings = {
        "linuxserver/proot-apps": [
            {"remote": "linuxserver/proot-apps", "name": "firefox", "full_name": "Firefox", "description": "d", "arch": "linux/amd64,linux/arm64", "icon": "firefox.svg", "disabled": False},
            {"remote": "linuxserver/proot-apps", "name": "armonly", "full_name": "Arm", "description": "", "arch": "linux/arm64", "icon": "", "disabled": False},
        ],
        "myorg/proot-apps": [
            {"remote": "myorg/proot-apps", "name": "gimp", "full_name": "GIMP", "description": "d", "arch": "linux/amd64", "icon": "gimp.svg", "disabled": False},
        ],
    }
    folder = prootapps.catalog_dir("cat1")
    stale = os.path.join(folder, "ghcr.io_linuxserver_proot-apps_old")
    os.makedirs(stale)
    status = prootapps.status_of("cat1")
    prootapps._sync_blocking(catalog, listings, status)

    firefox = os.path.join(folder, "ghcr.io_linuxserver_proot-apps_firefox")
    assert open(os.path.join(firefox, "SHALAYER")).read().strip() == FakeRegistry.digests["firefox"]
    assert os.path.exists(os.path.join(firefox, "app.tar.gz"))
    assert os.path.exists(os.path.join(folder, "ghcr.io_myorg_proot-apps_gimp", "app.tar.gz"))
    assert not os.path.exists(stale)
    assert status["state"] == "ready"
    assert status["present"]["ghcr.io_linuxserver_proot-apps_armonly"]["skipped"] == "not built for amd64"
    with open(os.path.join(folder, "metadata", "metadata.yml")) as handle:
        names = [entry["name"] for entry in yaml.safe_load(handle)["include"]]
    assert names == ["firefox", "ghcr.io/myorg/proot-apps:gimp", "armonly"]

    # A second sync with the same digests fetches nothing and keeps the packages.
    FakeRegistry.download_blob = lambda self, digest, destination: (_ for _ in ()).throw(AssertionError("fetched again"))
    prootapps._sync_blocking(catalog, listings, status)
    assert status["state"] == "ready"


def _plain_metadata(folder, apps):
    """The metadata file without the icons, which need the network."""
    os.makedirs(os.path.join(folder, "metadata", "img"), exist_ok=True)
    include = [{"name": prootapps.install_name(a["remote"], a["name"]), "full_name": a["full_name"]} for a in apps]
    with open(os.path.join(folder, "metadata", "metadata.yml"), "w") as handle:
        yaml.safe_dump({"include": include}, handle)


def test_the_catalog_is_the_users_own_else_the_first_groups():
    user_manager.write_group_file("staff", {"proot_catalog": "cat-staff"})
    user_manager.write_group_file("lab", {"proot_catalog": "cat-lab"})
    user_manager.write_group_file("plain", {"gpu": False})
    user_manager.write_user_file("alice", "", dict(user_manager.DEFAULT_USER_SETTINGS, groups=["plain", "lab", "staff"]))
    user_manager.write_user_file("bob", "", dict(user_manager.DEFAULT_USER_SETTINGS, groups=["staff"], proot_catalog="cat-own"))
    user_manager.write_user_file("carol", "", dict(user_manager.DEFAULT_USER_SETTINGS))
    user_manager.write_user_file("dan", "", dict(user_manager.DEFAULT_USER_SETTINGS, admin=True, proot_catalog="cat-own"))
    user_manager.load_users_and_groups()
    assert user_manager.get_effective_settings("alice")["proot_catalog"] == "cat-lab"
    assert user_manager.get_effective_settings("bob")["proot_catalog"] == "cat-own"
    assert user_manager.get_effective_settings("carol")["proot_catalog"] is None
    dan = user_manager.get_effective_settings("dan")
    assert dan["admin"] and dan["proot_catalog"] == "cat-own"


def test_a_session_mounts_its_catalog_read_only(tmp_path):
    state.proot_catalogs["cat1"] = ProotCatalog(id="cat1", name="Office")
    assert prootapps.mount_path_for({"proot_catalog": "missing"}) is None
    assert prootapps.mount_path_for({}) is None
    path = prootapps.mount_path_for({"proot_catalog": "cat1"})
    assert path == os.path.join(settings.proot_apps_path, "cat1")
    assert os.path.isdir(os.path.join(path, "metadata", "img"))
    app = InstalledApp(
        id="app-1", name="Firefox", logo="x", url="x", source="s", source_app_id="firefox", provider="docker",
        home_directories=True, users=["all"], groups=[], app_template="Default",
        provider_config={
            "image": "img:latest", "port": 3000, "type": "browser", "extensions": [],
            "nvidia_support": False, "dri3_support": False, "url_support": False, "open_support": False,
        },
    )
    spec = launch.build_launch_spec(
        app, "sess", base_env={}, extra_env=None, language=None, wayland_mode=False, gpu_config=None,
        host_mount_path=None, shared_files_path=None, proot_catalog_path=path,
    )
    assert spec.volumes[path] == {"bind": prootapps.MOUNT_PATH, "mode": "ro"}
    assert spec.env["PA_REPO_FOLDER"] == prootapps.MOUNT_PATH
