"""Registries, mirrors, and the credentials of what a node fetches.

Two files the administrator keeps on the node hold the credentials, in the
formats their own tools write, and neither leaves it: the shared store holds
no secret and the API shows none. `registry_auth_path` is a Docker
`config.json` whose `auths` serve the registries: session images and the VPN
gateway image, the digest checks and pins, and the packages of PRoot Apps
catalogs. `netrc_path` is a netrc whose machines serve plain HTTP hosts: app
stores and their autostart scripts, logos, and the metadata and icons of
catalogs. Both are read at every use, so a rotated credential needs no
restart. On Kubernetes the pull secrets the session PodTemplate names add
to the registry file (`registry_auths`).

`image_mirrors` names a mirror per upstream registry,
`lscr.io=registry.example/lscr,docker.io=registry.example/hub`, the way a
proxying registry exposes one upstream per project. Every pull goes through
`mirror`: records keep the upstream name, and the credential looked up is
the mirror's.
"""

from __future__ import annotations

import base64
import json
import logging
import netrc
import os
import re
from typing import Any

import httpx

from .settings import settings

logger = logging.getLogger(__name__)

#: Host Docker Hub's images are pulled from; the names the config file and references use for it.
DOCKER_HUB = "registry-1.docker.io"
_HUB_NAMES = {"docker.io", "index.docker.io", DOCKER_HUB}
_warned: set[str] = set()


# --- Image references -----------------------------------------------------------


def split_reference(image: str) -> tuple[str, str, str]:
    """Split an image reference into registry host, repository, and tag or digest.

    A reference without a host is Docker Hub's, with `library/` before a
    single-segment name; `docker.io` is named as it is pulled from.
    """
    name, _, digest = image.partition("@")
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = DOCKER_HUB, name
        if "/" not in repository:
            repository = f"library/{repository}"
    if registry in _HUB_NAMES:
        registry = DOCKER_HUB
    if digest:
        return registry, repository_of(repository), digest
    if ":" in repository.rsplit("/", 1)[-1]:
        repository, _, tag = repository.rpartition(":")
        return registry, repository, tag
    return registry, repository, "latest"


def repository_of(image: str) -> str:
    """Return an image reference without its tag or digest."""
    name = image.partition("@")[0]
    return name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name


def _mirrors() -> dict[str, str]:
    """Return the mirror of each upstream registry `image_mirrors` names."""
    mirrors: dict[str, str] = {}
    for item in (settings.image_mirrors or "").split(","):
        upstream, _, replacement = item.strip().partition("=")
        upstream, replacement = upstream.strip().lower(), replacement.strip().strip("/")
        if not upstream or not replacement:
            continue
        mirrors[DOCKER_HUB if upstream in _HUB_NAMES else upstream] = replacement
    return mirrors


def mirror(image: str) -> str:
    """Return the reference an image is pulled by: its mirror's when `image_mirrors` names one."""
    mirrors = _mirrors()
    if not mirrors:
        return image
    registry, repository, reference = split_reference(image)
    replacement = mirrors.get(registry)
    if not replacement:
        return image
    separator = "@" if reference.startswith("sha256:") else ":"
    return f"{replacement}/{repository}{separator}{reference}"


# --- Registry credentials ---------------------------------------------------------


def _host_of(key: str) -> str:
    """Return the host an `auths` key names, however the file spells it."""
    host = re.sub(r"^https?://", "", key.strip()).split("/", 1)[0].lower()
    return DOCKER_HUB if host in _HUB_NAMES else host


def parse_docker_config(document: Any, origin: str = "") -> dict[str, tuple[str, str]]:
    """Return the username and password per registry host of a Docker `config.json` document.

    Entries that name a credential helper or hold a token only are passed
    over, with a warning the first time for `origin`.
    """
    auths: dict[str, tuple[str, str]] = {}
    if not isinstance(document, dict):
        return auths
    if (document.get("credsStore") or document.get("credHelpers")) and origin not in _warned:
        _warned.add(origin)
        logger.warning("%s names a credential helper, which this server cannot run; only its `auths` serve.", origin)
    for key, entry in (document.get("auths") or {}).items():
        if not isinstance(entry, dict):
            continue
        username, password = entry.get("username"), entry.get("password")
        if entry.get("auth"):
            try:
                username, _, password = base64.b64decode(entry["auth"]).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                continue
        if username is None or password is None:
            continue
        auths[_host_of(str(key))] = (str(username), str(password))
    return auths


def file_auths() -> dict[str, tuple[str, str]]:
    """Return the credentials of `registry_auth_path`, none when there is no file."""
    path = settings.registry_auth_path
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Registry credentials not read from %s: %s", path, exc)
        return {}
    return parse_docker_config(document, path)


async def registry_auths() -> dict[str, tuple[str, str]]:
    """Return every registry credential this node holds: the backend's pull secrets under the file's."""
    from .providers import get_provider

    try:
        auths = dict(await get_provider().registry_credentials())
    except Exception as exc:  # noqa: BLE001 - the file alone then serves
        logger.warning("The backend's registry credentials were not read: %s", exc)
        auths = {}
    auths.update(file_auths())
    return auths


def credential(host: str, auths: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """Return the username and password for a registry host, or `None` without one."""
    return auths.get(_host_of(host))


def docker_auth(reference: str) -> dict[str, str] | None:
    """Return the `auth_config` the Docker SDK pulls `reference` with, from the file alone."""
    found = credential(split_reference(reference)[0], file_auths())
    return {"username": found[0], "password": found[1]} if found else None


def challenge(response: httpx.Response) -> tuple[str, dict[str, str]]:
    """Return the scheme and parameters of a response's `WWW-Authenticate`, `("", {})` without one."""
    scheme, _, rest = response.headers.get("WWW-Authenticate", "").strip().partition(" ")
    return scheme.lower(), dict(re.findall(r'(\w+)="([^"]*)"', rest))


def basic(found: tuple[str, str]) -> str:
    """Return the `Authorization` value of a username and password."""
    return "Basic " + base64.b64encode(f"{found[0]}:{found[1]}".encode()).decode()


# --- HTTP credentials -------------------------------------------------------------


def netrc_auth() -> httpx.Auth | None:
    """Return the auth that serves the hosts of `netrc_path` to an httpx client, `None` without a file."""
    path = settings.netrc_path
    if not os.path.exists(path):
        return None
    try:
        return httpx.NetRCAuth(path)
    except (OSError, netrc.NetrcParseError) as exc:
        logger.warning("HTTP credentials not read from %s: %s", path, exc)
        return None
