"""PRoot Apps catalogs: naming, the folder a sync writes, who gets which catalog, and the mount."""

import os

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
    assert prootapps.icon_file("myorg/proot-apps", "gimp.svg") == "myorg_proot-apps_gimp.svg"
    assert prootapps.is_remote("linuxserver/proot-apps") and not prootapps.is_remote("not a remote")


class FakeRegistry:
    """Stands in for the registry: one layer per tag, a known digest, a short package."""

    digests = {"firefox": "sha256:" + "a" * 64, "gimp": "sha256:" + "b" * 64}

    def __init__(self, _client, image):
        self.tag = image.rsplit(":", 1)[1]

    def layer_digest(self, _arch):
        return self.digests[self.tag]

    def download_blob(self, _digest, destination):
        with open(destination, "wb") as handle:
            handle.write(b"tar")
        return 3


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
