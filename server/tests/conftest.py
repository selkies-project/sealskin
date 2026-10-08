"""Shared fixtures: point every path setting at a temporary directory."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import branding, cluster, docker_utils, quota, store  # noqa: E402
from app.providers import provider_class  # noqa: E402
from app.settings import settings  # noqa: E402
from app.state import state  # noqa: E402

PROXY_SECRET = "test-proxy-secret"
#: Headers of a request that came through the node's proxy from the web app's own page.
WEB = {"X-SealSkin-Secret": PROXY_SECRET, "Sec-Fetch-Site": "same-origin"}

def _no_docker(*_args, **_kwargs):
    """Stand in for `docker.from_env` in tests."""
    raise RuntimeError("Tests do not talk to Docker.")


PATH_SETTINGS = [
    "installed_apps_path",
    "app_stores_path",
    "app_templates_path",
    "default_app_templates_path",
    "upload_dir",
    "autostart_cache_path",
    "app_store_cache_path",
    "proot_catalogs_path",
    "proot_apps_path",
    "keys_base_path",
    "groups_base_path",
    "storage_path",
    "app_icons_path",
    "branding_path",
    "home_templates_path",
    "public_storage_path",
    "public_shares_metadata_path",
    "sessions_db_path",
    "sso_keys_path",
    "cluster_path",
    "node_state_path",
    "shared_files_path",
    "root_token_path",
]


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Redirect every filesystem setting into ``tmp_path`` and reset state."""
    base = tmp_path / "config"
    layout = {
        "installed_apps_path": base / "installed_apps.yml",
        "app_stores_path": base / "app_stores.yml",
        "app_templates_path": base / "app_templates",
        "default_app_templates_path": base / "default_templates",
        "upload_dir": tmp_path / "storage" / "uploads",
        "autostart_cache_path": base / "autostart_cache",
        "app_store_cache_path": base / "app_stores_cache",
        "proot_catalogs_path": base / "proot_catalogs.yml",
        "proot_apps_path": tmp_path / "storage" / "proot_apps",
        "keys_base_path": base / "keys",
        "groups_base_path": base / "groups",
        "storage_path": tmp_path / "storage",
        "app_icons_path": tmp_path / "storage" / "icons",
        "branding_path": base / "branding",
        "home_templates_path": tmp_path / "storage" / "home_templates",
        "public_storage_path": tmp_path / "storage" / "public",
        "public_shares_metadata_path": base / "public_shares.yml",
        "sessions_db_path": base / "sessions.yml",
        "sso_keys_path": base / "sso_keys.yml",
        "cluster_path": base / "cluster",
        "node_state_path": base / "node",
        "shared_files_path": tmp_path / "storage" / "shared_store",
        "root_token_path": base / "root_token",
    }
    for name, path in layout.items():
        monkeypatch.setattr(settings, name, str(path))
    monkeypatch.setattr(settings, "app_resource_path", "https://example.invalid/apps.yml")
    monkeypatch.setattr(settings, "default_provider", "docker")
    provider_class.cache_clear()
    store.reset()
    store.use_peer(None)
    for table in (cluster.NODES, cluster.POOLS, cluster.PEERS, cluster.HOMES, quota.USAGE, quota._own):
        table.clear()
    quota._dirty.clear()
    monkeypatch.setattr(state, "proxy_secret", PROXY_SECRET)
    branding.load()
    # Never the machine's real Docker: a server started by a test would take a live server's sessions for its own.
    monkeypatch.setattr(docker_utils, "_CLIENT", None)
    monkeypatch.setattr(docker_utils.docker, "from_env", _no_docker)
    state.installed_records.clear()
    state.installed_apps.clear()
    state.app_stores.clear()
    state.store_entries.clear()
    state.app_templates.clear()
    state.template_files.clear()
    state.sessions.clear()
    state.available_gpus.clear()
    state.path_prefix_map.clear()
    yield
    state.installed_records.clear()
    state.installed_apps.clear()
    state.app_stores.clear()
    state.store_entries.clear()
    state.app_templates.clear()
    state.template_files.clear()
    state.sessions.clear()


STORE_APP = {
    "id": "firefox",
    "name": "Firefox",
    "logo": "https://example.invalid/firefox.png",
    "url": "https://example.invalid/firefox",
    "provider": "docker",
    "provider_config": {
        "image": "lscr.io/linuxserver/firefox:latest",
        "port": 3000,
        "nvidia_support": True,
        "dri3_support": True,
        "type": "browser",
        "url_support": True,
        "open_support": False,
        "extensions": [["html", "htm"], "pdf"],
        "autostart": False,
    },
}


@pytest.fixture
def store_with_firefox():
    """Register one store whose cache contains the Firefox entry."""
    import yaml

    from app import config_store
    from app.models import AppStore

    state.app_stores.append(AppStore(name="Test Store", url="https://example.invalid/apps.yml"))
    config_store.ensure_config_dir()
    with open(config_store.store_cache_file("Test Store"), "w", encoding="utf-8") as handle:
        yaml.safe_dump({"apps": [STORE_APP]}, handle)
    config_store.load_store_entries()
    return STORE_APP
