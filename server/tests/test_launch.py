"""Launch spec assembly."""

import os

import pytest
from fastapi import HTTPException

from app import launch
from app.models import InstalledApp
from app.state import state


def make_app(**overrides):
    data = {
        "id": "app-1",
        "name": "Firefox",
        "logo": "x",
        "url": "x",
        "source": "Test Store",
        "source_app_id": "firefox",
        "provider": "docker",
        "home_directories": True,
        "users": ["all"],
        "groups": [],
        "app_template": "Default",
        "provider_config": {
            "image": "img:latest",
            "port": 3000,
            "nvidia_support": False,
            "dri3_support": True,
            "type": "browser",
            "url_support": True,
            "open_support": False,
            "extensions": [],
            "env": [{"name": "APP_ENV", "value": "1"}],
            "docker_overrides": {"devices": ["/dev/snd"]},
        },
    }
    data.update(overrides)
    return InstalledApp(**data)


def test_build_launch_spec_env_volumes_and_overrides(tmp_path):
    state.app_templates["Default"] = {
        "name": "Default",
        "settings": {"TITLE": "Hi", "DOCKER_DEVICES": "/dev/dri/renderD128", "DOCKER_SHM_SIZE": "2g"},
    }
    home = tmp_path / "home"
    shared = tmp_path / "shared"
    home.mkdir()
    app = make_app()
    spec = launch.build_launch_spec(
        app,
        "sess",
        base_env=launch.session_base_env("sess", "u", "p", None),
        extra_env={"SEALSKIN_URL": "https://example.com"},
        language="de_DE.UTF-8",
        wayland_mode=True,
        gpu_config={"type": "dri3", "device": "/dev/dri/renderD128"},
        host_mount_path=str(home),
        shared_files_path=str(shared),
    )
    assert spec.env["SUBFOLDER"] == "/sess/"
    assert spec.env["PIXELFLUX_WAYLAND"] == "true"
    assert spec.env["TITLE"] == "Hi"
    assert "DOCKER_DEVICES" not in spec.env
    assert spec.env["LC_ALL"] == "de_DE.UTF-8"
    assert spec.env["APP_ENV"] == "1"
    assert spec.env["DRI_NODE"] == "/dev/dri/renderD128"
    assert spec.launch_context == {"type": "url", "value": "https://example.com"}
    overrides = spec.app_config["provider_config"]["docker_overrides"]
    assert overrides["devices"] == ["/dev/snd", "/dev/dri/renderD128"]
    assert overrides["shm_size"] == "2g"
    assert set(spec.volumes) == {str(home), str(shared)}
    assert os.path.isdir(home / "Desktop" / "files")
    kwargs = spec.provider_kwargs("sess")
    assert kwargs["env_vars"] is spec.env and "is_collaboration" not in kwargs


def test_extract_docker_overrides_parsing():
    result = launch.extract_docker_overrides(
        {
            "DOCKER_PRIVILEGED": "true",
            "DOCKER_CAP_ADD": "SYS_ADMIN, NET_ADMIN",
            "DOCKER_ENV": "A=1,B=2",
            "DOCKER_CPU_SHARES": "notanumber",
            "NOT_DOCKER": "x",
        }
    )
    assert result == {
        "privileged": True,
        "cap_add": ["SYS_ADMIN", "NET_ADMIN"],
        "environment": {"A": "1", "B": "2"},
    }


def test_validate_gpu_rejects_unsupported():
    state.available_gpus.append({"device": "/dev/dri/renderD128", "driver": "nvidia", "type": "nvidia", "index": 0})
    app = make_app()
    assert launch.validate_gpu(None, {"gpu": True}, app) is None
    assert launch.validate_gpu("/dev/dri/renderD128", {"gpu": False}, app) is None
    with pytest.raises(HTTPException):
        launch.validate_gpu("/dev/dri/renderD128", {"gpu": True}, app)
    with pytest.raises(HTTPException):
        launch.validate_gpu("/dev/dri/renderD999", {"gpu": True}, app)
    assert launch.gpu_for_app({"type": "nvidia"}, app) is None


def test_collaboration_initial_tokens():
    tokens = launch.collaboration_initial_tokens(
        {
            "controller_token": "c",
            "controller_slot": 1,
            "mk_owner_token": "v1",
            "viewers": [{"token": "v1", "slot": None}, {"token": "v2", "slot": 2}],
        }
    )
    assert tokens["c"] == {"role": "controller", "slot": 1, "mk_control": False}
    assert tokens["v1"]["mk_control"] is True and tokens["v2"]["mk_control"] is False


def test_resolve_timezone_prefers_valid_client_zone(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert launch.resolve_timezone("America/New_York") == "America/New_York"
    assert launch.resolve_timezone("UTC") == "UTC"
    assert launch.resolve_timezone("America/Argentina/Buenos_Aires") == "America/Argentina/Buenos_Aires"
    assert launch.resolve_timezone("Etc/GMT+5") == "Etc/GMT+5"


def test_resolve_timezone_falls_back_through_candidates(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Berlin")
    assert launch.resolve_timezone(None, "Asia/Tokyo") == "Asia/Tokyo"
    assert launch.resolve_timezone("not a zone", "Asia/Tokyo") == "Asia/Tokyo"
    assert launch.resolve_timezone(None) == "Europe/Berlin"
    assert launch.resolve_timezone("../etc/passwd") == "Europe/Berlin"
    assert launch.resolve_timezone("A" * 65) == "Europe/Berlin"
    assert launch.resolve_timezone(42) == "Europe/Berlin"
    monkeypatch.setenv("TZ", ":/etc/localtime")
    assert launch.resolve_timezone(None) == "Etc/UTC"
    monkeypatch.delenv("TZ")
    assert launch.resolve_timezone(None) == "Etc/UTC"
    assert launch.resolve_timezone() == "Etc/UTC"


def test_session_base_env_timezone(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    assert launch.session_base_env("s", "u", "p", None)["TZ"] == "Etc/UTC"
    env = launch.session_base_env("s", "u", "p", None, "Australia/Sydney")
    assert env["TZ"] == "Australia/Sydney"
    assert launch.session_base_env("s", "u", "p", None, "bogus zone")["TZ"] == "Etc/UTC"


def test_hardening_env_from_user_settings():
    assert launch.hardening_env(None) == {}
    assert launch.hardening_env({"harden_container": False, "harden_openbox": False}) == {}
    assert launch.hardening_env({"harden_container": True}) == {"HARDEN_DESKTOP": "true"}
    assert launch.hardening_env({"harden_container": True, "harden_openbox": True}) == {
        "HARDEN_DESKTOP": "true",
        "HARDEN_OPENBOX": "true",
    }


def test_build_launch_spec_forced_env_wins_and_legacy_keys_migrate(tmp_path):
    state.app_templates["Default"] = {
        "name": "Default",
        "settings": {
            "HARDEN_DESKTOP": "false",
            "SELKIES_H264_CRF": "10-40",
            "SELKIES_CLIPBOARD_IN_ENABLED": "false",
        },
    }
    app = make_app(provider_config={
        "image": "img:latest",
        "port": 3000,
        "nvidia_support": False,
        "dri3_support": True,
        "type": "browser",
        "url_support": True,
        "open_support": False,
        "extensions": [],
        "env": [{"name": "HARDEN_OPENBOX", "value": "false"}],
        "docker_overrides": {},
    })
    spec = launch.build_launch_spec(
        app,
        "sess",
        base_env=launch.session_base_env("sess", "u", "p", None),
        extra_env=None,
        language=None,
        wayland_mode=True,
        gpu_config=None,
        host_mount_path=None,
        shared_files_path=None,
        forced_env=launch.hardening_env({"harden_container": True, "harden_openbox": True}),
    )
    assert spec.env["HARDEN_DESKTOP"] == "true"
    assert spec.env["HARDEN_OPENBOX"] == "true"
    assert spec.env["SELKIES_VIDEO_CRF"] == "10-40"
    assert "SELKIES_H264_CRF" not in spec.env
    assert spec.env["SELKIES_ENABLE_CLIPBOARD"] == "out"
    assert "SELKIES_CLIPBOARD_IN_ENABLED" not in spec.env
