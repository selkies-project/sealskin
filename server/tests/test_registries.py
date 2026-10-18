"""Registries: the credential files, the mirror rewrite, and what a registry's challenge asks for."""

import base64
import json

import httpx
import pytest

from app import registries
from app.settings import settings


def _config(tmp_path, monkeypatch, document):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(document))
    monkeypatch.setattr(settings, "registry_auth_path", str(path))
    return path


def test_docker_config_serves_hosts_however_it_spells_them(tmp_path, monkeypatch):
    _config(
        tmp_path,
        monkeypatch,
        {
            "auths": {
                "https://index.docker.io/v1/": {"auth": base64.b64encode(b"hubuser:hubpass").decode()},
                "registry.example:5000": {"username": "u", "password": "p"},
                "ghcr.io": {"identitytoken": "only-a-token"},
            },
            "credsStore": "desktop",
        },
    )
    auths = registries.file_auths()
    assert auths == {"registry-1.docker.io": ("hubuser", "hubpass"), "registry.example:5000": ("u", "p")}
    assert registries.credential("docker.io", auths) == ("hubuser", "hubpass")
    assert registries.credential("ghcr.io", auths) is None
    assert registries.docker_auth("nginx") == {"username": "hubuser", "password": "hubpass"}
    assert registries.docker_auth("registry.example:5000/team/app:v2") == {"username": "u", "password": "p"}
    assert registries.docker_auth("lscr.io/linuxserver/firefox") is None


def test_no_file_means_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "registry_auth_path", str(tmp_path / "missing.json"))
    assert registries.file_auths() == {}
    assert registries.docker_auth("nginx") is None


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("lscr.io/linuxserver/firefox:latest", "registry.example/lscr/linuxserver/firefox:latest"),
        ("nginx", "registry.example/hub/library/nginx:latest"),
        ("docker.io/linuxserver/webtop:ubuntu", "registry.example/hub/linuxserver/webtop:ubuntu"),
        ("ghcr.io/x/y@sha256:" + "b" * 64, "registry.example/ghcr/x/y@sha256:" + "b" * 64),
        ("quay.io/team/app:1", "quay.io/team/app:1"),
    ],
)
def test_mirrors_rewrite_the_registry_of_a_reference(monkeypatch, image, expected):
    monkeypatch.setattr(
        settings, "image_mirrors", "lscr.io=registry.example/lscr, docker.io=registry.example/hub/,ghcr.io=registry.example/ghcr"
    )
    assert registries.mirror(image) == expected


def test_without_mirrors_a_reference_is_left_as_it_is(monkeypatch):
    monkeypatch.setattr(settings, "image_mirrors", "")
    assert registries.mirror("nginx") == "nginx"


def test_netrc_signs_its_hosts_in_and_no_other(tmp_path, monkeypatch):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine git.example login bob password s3cret\n")
    monkeypatch.setattr(settings, "netrc_path", str(netrc))
    seen = {}

    def handler(request):
        seen[request.url.host] = request.headers.get("Authorization")
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler), auth=registries.netrc_auth()) as client:
        client.get("https://git.example/raw/apps.yml")
        client.get("https://raw.githubusercontent.com/x/y/apps.yml")
    assert seen["git.example"] == "Basic " + base64.b64encode(b"bob:s3cret").decode()
    assert seen["raw.githubusercontent.com"] is None
    monkeypatch.setattr(settings, "netrc_path", str(tmp_path / "none"))
    assert registries.netrc_auth() is None


def test_a_challenge_is_read_by_scheme():
    bearer = httpx.Response(401, headers={"WWW-Authenticate": 'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:x/y:pull"'})
    assert registries.challenge(bearer) == ("bearer", {"realm": "https://ghcr.io/token", "service": "ghcr.io", "scope": "repository:x/y:pull"})
    assert registries.challenge(httpx.Response(401, headers={"WWW-Authenticate": 'Basic realm="Registry"'})) == ("basic", {"realm": "Registry"})
    assert registries.challenge(httpx.Response(401)) == ("", {})
    assert registries.basic(("u", "p")) == "Basic dTpw"
