"""PRoot Apps catalogs: folders of application packages that sessions install from.

A catalog is a shared record of `proot_catalogs.yml` naming the apps an
administrator picked from one or more remotes. A remote is a GitHub
`owner/repo` fork of linuxserver/proot-apps: its `metadata/metadata.yml`
lists the apps and each app is a one-layer image tagged with its name under
`ghcr.io/<owner>/<repo>`. Every node keeps its own copy of a catalog's
content under `proot_apps_path/<id>`, in the layout `proot-apps` reads
through `PA_REPO_FOLDER`:

    metadata/metadata.yml          the apps, for the graphical installer
    metadata/img/<icon>            their icons
    ghcr.io_<owner>_<repo>_<app>/  app.tar.gz, the image's layer, and SHALAYER, its digest

A session of a user whose effective settings name a catalog mounts that
folder read-only at `MOUNT_PATH` with `PA_REPO_FOLDER` pointing at it, so
`proot-apps install`, `update`, and the installer take everything from the
folder and reach no registry. The content follows the record: a node syncs a
catalog whenever the record's `revision` is newer than its copy, and the
auto-update job syncs every catalog that asks for it; a sync fetches only the
apps whose layer digest changed, and removes the folders of apps no longer
picked. The record is small and shared; the content and the state of the
copy (`node_state_path/proot-apps/<id>.yml`) are this node's own.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import re
import shutil
import threading
import time
from typing import Any

import httpx
import yaml
from pydantic import ValidationError

from . import persistence
from .fsutil import safe_join, safe_rmtree
from .models import ProotCatalog, ProotRemoteApp
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

#: Where a session finds its catalog; `PA_REPO_FOLDER` names it.
MOUNT_PATH = "/mnt/proot-apps"

#: Accept types a registry answers a manifest request with.
MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)

#: Seconds a remote's metadata is served from memory before it is fetched again.
REMOTE_CACHE_SECONDS = 900

_REMOTE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_remote_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_locks: dict[str, asyncio.Lock] = {}
_tasks: dict[str, asyncio.Task] = {}
#: Set at shutdown so a download in a thread stops at its next chunk.
_stopping = threading.Event()


# --- Names and places ---------------------------------------------------------


def node_arch() -> str:
    """Return this node's architecture the way image platforms name it."""
    machine = platform.machine()
    return {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)


def is_remote(remote: str) -> bool:
    """Tell whether `remote` is a GitHub `owner/repo`."""
    return bool(_REMOTE.match(remote or ""))


def remote_image(remote: str, name: str) -> str:
    """Return the image reference of an app of a remote."""
    return f"ghcr.io/{remote.lower()}:{name}"


def image_folder(image: str) -> str:
    """Return the folder `proot-apps` keeps an image under: the reference with `/` and `:` as `_`."""
    return image.replace("/", "_").replace(":", "_")


def install_name(remote: str, name: str) -> str:
    """Return what a user types to install an app: the short name from the default remote, else the image."""
    return name if remote.lower() == settings.proot_apps_remote.lower() else remote_image(remote, name)


def metadata_url(remote: str) -> str:
    """Return the URL of a remote's metadata file."""
    return f"https://raw.githubusercontent.com/{remote}/master/metadata/metadata.yml"


def icon_url(remote: str, icon: str) -> str:
    """Return the URL of an icon the remote's metadata names."""
    return f"https://raw.githubusercontent.com/{remote}/master/metadata/img/{icon}"


def icon_file(remote: str, icon: str) -> str:
    """Return the file name an icon is kept under in a catalog, distinct per remote."""
    if remote.lower() == settings.proot_apps_remote.lower():
        return icon
    return f"{remote.lower().replace('/', '_')}_{icon}"


def catalog_dir(catalog_id: str) -> str:
    """Return this node's folder of a catalog's content."""
    return safe_join(settings.proot_apps_path, catalog_id)


def status_path(catalog_id: str) -> str:
    """Return the file holding this node's sync state of a catalog."""
    return safe_join(settings.node_state_path, "proot-apps", f"{catalog_id}.yml")


# --- Records -----------------------------------------------------------------


def load_catalogs() -> None:
    """Read the shared catalog records into `state.proot_catalogs`."""
    records = persistence.read_yaml(settings.proot_catalogs_path, []) or []
    loaded: dict[str, ProotCatalog] = {}
    for item in records if isinstance(records, list) else []:
        try:
            catalog = ProotCatalog(**item)
        except (ValidationError, TypeError) as exc:
            logger.warning("Skipping a PRoot Apps catalog record that does not validate: %s", exc)
            continue
        loaded[catalog.id] = catalog
    state.proot_catalogs = loaded


async def save_catalogs() -> None:
    """Write `state.proot_catalogs` to the shared record."""
    await persistence.write_yaml(
        settings.proot_catalogs_path, [c.model_dump() for c in state.proot_catalogs.values()]
    )


def _read_status(catalog_id: str) -> dict[str, Any]:
    """Return this node's stored sync state of a catalog, empty before the first sync."""
    try:
        return persistence.read_yaml(status_path(catalog_id), {}) or {}
    except (yaml.YAMLError, OSError):
        return {}


def _write_status(catalog_id: str, status: dict[str, Any]) -> None:
    """Keep this node's sync state of a catalog."""
    persistence.write_yaml_sync(status_path(catalog_id), status)


def status_of(catalog_id: str) -> dict[str, Any]:
    """Return this node's sync state of a catalog, loading it on first use."""
    if catalog_id not in state.proot_status:
        stored = _read_status(catalog_id)
        state.proot_status[catalog_id] = dict(
            {"state": "pending", "message": "", "synced_revision": 0, "present": {}, "size": 0}, **stored
        )
        if stored.get("state") == "syncing":
            state.proot_status[catalog_id]["state"] = "pending"
    return state.proot_status[catalog_id]


def mount_path_for(effective_settings: dict[str, Any] | None) -> str | None:
    """Return the folder a user's sessions mount as their catalog, or `None` without one.

    The folder is made when the node has not synced the catalog yet, so the
    session mounts an empty catalog rather than failing.
    """
    catalog_id = (effective_settings or {}).get("proot_catalog")
    if not catalog_id or catalog_id not in state.proot_catalogs:
        return None
    try:
        path = catalog_dir(catalog_id)
    except ValueError:
        return None
    os.makedirs(os.path.join(path, "metadata", "img"), exist_ok=True, mode=0o755)
    return path


# --- Remotes -----------------------------------------------------------------


def _parse_metadata(raw: bytes, remote: str) -> list[dict[str, Any]]:
    """Return the apps a remote's metadata lists, as plain dicts."""
    document = yaml.safe_load(raw.decode("utf-8")) or {}
    entries = document.get("include") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise ValueError("The metadata has no `include` list.")
    apps: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        icon = str(entry.get("icon") or "")
        apps.append(
            {
                "remote": remote,
                "name": str(entry["name"]),
                "full_name": str(entry.get("full_name") or entry["name"]).strip(),
                "description": str(entry.get("description") or "").strip(),
                "arch": str(entry.get("arch") or ""),
                "icon": icon,
                "disabled": bool(entry.get("disabled")),
            }
        )
    return apps


async def fetch_remote(remote: str, refresh: bool = False) -> list[dict[str, Any]]:
    """Return the apps a remote publishes, from memory unless stale or `refresh`.

    Raises:
        ValueError: For a remote that is not `owner/repo` or whose metadata is not a list of apps.
        httpx.HTTPError: When the metadata cannot be fetched.
    """
    if not is_remote(remote):
        raise ValueError("A remote is a GitHub owner/repo.")
    cached = _remote_cache.get(remote.lower())
    if cached and not refresh and time.time() - cached[0] < REMOTE_CACHE_SECONDS:
        return cached[1]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(metadata_url(remote))
        response.raise_for_status()
    apps = _parse_metadata(response.content, remote)
    _remote_cache[remote.lower()] = (time.time(), apps)
    return apps


def remote_apps(remote: str, apps: list[dict[str, Any]]) -> list[ProotRemoteApp]:
    """Return a remote's apps as API models, icons as URLs."""
    return [
        ProotRemoteApp(**dict(app, icon=icon_url(remote, app["icon"]) if app["icon"] else ""))
        for app in apps
    ]


# --- The registry --------------------------------------------------------------


def _split_reference(image: str) -> tuple[str, str, str]:
    """Split an image reference into registry host, repository, and tag."""
    first, _, rest = image.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = "registry-1.docker.io", image
        if "/" not in repository:
            repository = f"library/{repository}"
    repository, _, tag = repository.rpartition(":") if ":" in repository.rsplit("/", 1)[-1] else (repository, "", "")
    return registry, repository, tag or "latest"


class _Registry:
    """Anonymous pull access to one repository of a distribution registry."""

    def __init__(self, client: httpx.Client, image: str) -> None:
        self.client = client
        self.registry, self.repository, self.tag = _split_reference(image)
        self.headers: dict[str, str] = {"Accept": ", ".join(MANIFEST_TYPES)}

    def _url(self, kind: str, reference: str) -> str:
        return f"https://{self.registry}/v2/{self.repository}/{kind}/{reference}"

    def _authorize(self, response: httpx.Response) -> bool:
        """Get the anonymous token a 401 asks for; `False` when the registry offers none."""
        challenge = dict(re.findall(r'(\w+)="([^"]*)"', response.headers.get("WWW-Authenticate", "")))
        if "realm" not in challenge:
            return False
        token = self.client.get(
            challenge["realm"], params={k: v for k, v in challenge.items() if k in ("service", "scope")}
        )
        token.raise_for_status()
        body = token.json()
        self.headers["Authorization"] = f"Bearer {body.get('token') or body.get('access_token')}"
        return True

    def manifest(self, reference: str) -> dict[str, Any]:
        """Return a manifest or index by tag or digest."""
        response = self.client.get(self._url("manifests", reference), headers=self.headers)
        if response.status_code == 401 and "Authorization" not in self.headers and self._authorize(response):
            response = self.client.get(self._url("manifests", reference), headers=self.headers)
        if response.status_code == 404:
            raise ValueError(f"{self.repository}:{reference} is not on {self.registry}.")
        response.raise_for_status()
        return response.json()

    def layer_digest(self, arch: str) -> str:
        """Return the digest of the first layer of the tag's image for `arch`.

        Raises:
            ValueError: When the tag is unknown, built for other architectures only, or has no layer.
        """
        document = self.manifest(self.tag)
        if "manifests" in document:
            # Attestation manifests carry annotations and no platform worth running.
            candidates = [m for m in document["manifests"] if not m.get("annotations")]
            if len(candidates) > 1:
                candidates = [m for m in candidates if (m.get("platform") or {}).get("architecture") == arch]
            if not candidates:
                raise ValueError(f"{self.repository}:{self.tag} is not built for {arch}.")
            document = self.manifest(candidates[0]["digest"])
        layers = document.get("layers") or []
        if not layers or not layers[0].get("digest"):
            raise ValueError(f"{self.repository}:{self.tag} has no layer to install.")
        return str(layers[0]["digest"])

    def download_blob(self, digest: str, destination: str) -> int:
        """Stream a blob to `destination`, checking its digest; returns its size.

        Raises:
            ValueError: When the bytes do not hash to `digest`.
        """
        algorithm, _, expected = digest.partition(":")
        hasher = hashlib.new(algorithm or "sha256")
        size = 0
        part = destination + ".part"
        for attempt in range(2):
            with self.client.stream("GET", self._url("blobs", digest), headers=self.headers) as response:
                if response.status_code == 401 and attempt == 0 and self._authorize(response):
                    continue
                response.raise_for_status()
                with open(part, "wb") as handle:
                    for chunk in response.iter_bytes(1 << 16):
                        if _stopping.is_set():
                            raise OSError("The server is shutting down.")
                        hasher.update(chunk)
                        handle.write(chunk)
                        size += len(chunk)
            break
        if hasher.hexdigest() != expected:
            os.remove(part)
            raise ValueError(f"The package of {self.repository}:{self.tag} did not match its digest.")
        os.replace(part, destination)
        return size


# --- Syncing -------------------------------------------------------------------


def _write_metadata(folder: str, apps: list[dict[str, Any]], client: httpx.Client, status: dict[str, Any]) -> None:
    """Write the catalog's `metadata/metadata.yml` and fetch the icons it names that are missing."""
    metadata_dir = os.path.join(folder, "metadata")
    img_dir = os.path.join(metadata_dir, "img")
    os.makedirs(img_dir, exist_ok=True, mode=0o755)
    include = []
    for app in apps:
        entry: dict[str, Any] = {
            "name": install_name(app["remote"], app["name"]),
            "full_name": app["full_name"],
            "arch": app["arch"],
            "description": app["description"],
        }
        if app["icon"]:
            icon = icon_file(app["remote"], app["icon"])
            entry["icon"] = icon
            target = os.path.join(img_dir, icon)
            if not os.path.exists(target):
                try:
                    response = client.get(icon_url(app["remote"], app["icon"]))
                    response.raise_for_status()
                    with open(target + ".part", "wb") as handle:
                        handle.write(response.content)
                    os.replace(target + ".part", target)
                except (httpx.HTTPError, OSError) as exc:
                    status["message"] = f"Icon of {app['name']} not fetched: {exc}"
        if app["disabled"]:
            entry["disabled"] = True
        include.append(entry)
    persistence.write_yaml_sync(os.path.join(metadata_dir, "metadata.yml"), {"include": include})


def _folder_size(folder: str) -> int:
    """Return the bytes under a folder."""
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _sync_blocking(catalog: ProotCatalog, listings: dict[str, list[dict[str, Any]]], status: dict[str, Any]) -> None:
    """Bring this node's copy of a catalog to what the record names; runs in a thread.

    `status` is the live status dict and is updated as the sync goes.
    """
    arch = node_arch()
    folder = catalog_dir(catalog.id)
    os.makedirs(folder, exist_ok=True, mode=0o755)
    wanted: list[dict[str, Any]] = []
    missing: list[str] = []
    for app in catalog.apps:
        listed = next((a for a in listings.get(app.remote.lower(), []) if a["name"] == app.name), None)
        if listed is None:
            missing.append(install_name(app.remote, app.name))
            listed = {
                "remote": app.remote,
                "name": app.name,
                "full_name": app.name,
                "description": "",
                "arch": "",
                "icon": "",
                "disabled": False,
            }
        wanted.append(listed)
    present: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    status.update(total=len(wanted), done=0, current="")
    with httpx.Client(timeout=httpx.Timeout(30.0, read=300.0), follow_redirects=True) as client:
        _write_metadata(folder, wanted, client, status)
        for app in wanted:
            image = remote_image(app["remote"], app["name"])
            name = image_folder(image)
            status["current"] = app["name"]
            app_dir = os.path.join(folder, name)
            record: dict[str, Any] = {"image": image, "remote": app["remote"], "name": app["name"]}
            if app["arch"] and f"linux/{arch}" not in app["arch"].split(","):
                record["skipped"] = f"not built for {arch}"
                present[name] = record
                status["done"] += 1
                continue
            try:
                registry = _Registry(client, image)
                digest = registry.layer_digest(arch)
                sha_file = os.path.join(app_dir, "SHALAYER")
                package = os.path.join(app_dir, "app.tar.gz")
                current = open(sha_file, encoding="utf-8").read().strip() if os.path.exists(sha_file) else ""
                if current != digest or not os.path.exists(package):
                    os.makedirs(app_dir, exist_ok=True, mode=0o755)
                    size = registry.download_blob(digest, package)
                    with open(sha_file + ".part", "w", encoding="utf-8") as handle:
                        handle.write(digest + "\n")
                    os.replace(sha_file + ".part", sha_file)
                    record["fetched_at"] = time.time()
                    logger.info("Fetched %s (%d bytes) into catalog '%s'.", image, size, catalog.name)
                record["digest"] = digest
                record["size"] = os.path.getsize(package)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                failures.append(f"{app['name']}: {exc}")
                logger.warning("Could not fetch %s for catalog '%s': %s", image, catalog.name, exc)
                if os.path.exists(os.path.join(app_dir, "SHALAYER")):
                    record["digest"] = open(os.path.join(app_dir, "SHALAYER"), encoding="utf-8").read().strip()
                    record["size"] = os.path.getsize(os.path.join(app_dir, "app.tar.gz"))
                else:
                    shutil.rmtree(app_dir, ignore_errors=True)
                    record["skipped"] = str(exc)
            present[name] = record
            status["done"] += 1
    # Folders of apps no longer picked go; loose files and the metadata stay.
    for entry in os.listdir(folder):
        path = os.path.join(folder, entry)
        if entry != "metadata" and entry not in present and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    status.update(present=present, size=_folder_size(folder), current="")
    notes = []
    if missing:
        notes.append("not listed by their remote: " + ", ".join(missing))
    if failures:
        notes.append("; ".join(failures))
    if failures:
        status["state"] = "error"
        status["message"] = "; ".join(notes)
    else:
        status["state"] = "ready"
        status["message"] = "; ".join(notes) or status.get("message", "")


async def sync(catalog: ProotCatalog) -> None:
    """Sync this node's copy of a catalog with the record, one sync per catalog at a time.

    A record changed while its sync ran is synced again before the lock is let go.
    """
    lock = _locks.setdefault(catalog.id, asyncio.Lock())
    async with lock:
        while True:
            current = state.proot_catalogs.get(catalog.id)
            if current is None:
                return
            await _sync_once(current)
            if state.proot_catalogs.get(catalog.id) in (None, current):
                return


async def _sync_once(catalog: ProotCatalog) -> None:
    """Sync this node's copy of a catalog with one record, and keep the outcome."""
    status = status_of(catalog.id)
    status.update(state="syncing", message="", done=0, total=len(catalog.apps), current="")
    listings: dict[str, list[dict[str, Any]]] = {}
    for remote in sorted({app.remote.lower() for app in catalog.apps}):
        try:
            listings[remote] = await fetch_remote(remote)
        except (httpx.HTTPError, ValueError) as exc:
            status["message"] = f"Metadata of {remote} not fetched: {exc}"
            logger.warning("Could not fetch the PRoot Apps metadata of %s: %s", remote, exc)
    try:
        await asyncio.to_thread(_sync_blocking, catalog, listings, status)
        status["synced_revision"] = catalog.revision
    except Exception as exc:  # noqa: BLE001 - the status carries the error
        status.update(state="error", message=str(exc), current="")
        logger.warning("Syncing PRoot Apps catalog '%s' failed: %s", catalog.name, exc)
    status["synced_at"] = time.time()
    try:
        await asyncio.to_thread(_write_status, catalog.id, status)
    except OSError as exc:
        logger.warning("Could not keep the sync state of catalog '%s': %s", catalog.name, exc)


def start_sync(catalog: ProotCatalog) -> None:
    """Sync a catalog in the background unless a sync of it is already queued."""
    task = _tasks.get(catalog.id)
    if task and not task.done():
        return
    _tasks[catalog.id] = asyncio.create_task(sync(catalog))


def reconcile() -> None:
    """Make this node's copies follow the records: sync the stale, remove the deleted.

    Called on the event loop: the work runs in tasks and threads.
    """
    for catalog in state.proot_catalogs.values():
        status = status_of(catalog.id)
        try:
            folder = catalog_dir(catalog.id)
        except ValueError:
            continue
        if status.get("synced_revision", 0) < catalog.revision or not os.path.isdir(folder):
            start_sync(catalog)
    try:
        folders = os.listdir(settings.proot_apps_path)
    except OSError:
        folders = []
    for entry in folders:
        if entry not in state.proot_catalogs and os.path.isdir(os.path.join(settings.proot_apps_path, entry)):
            asyncio.create_task(discard(entry))


def _remove_content(catalog_id: str) -> None:
    """Delete a catalog's folder and sync state file."""
    folder = catalog_dir(catalog_id)
    if os.path.isdir(folder):
        safe_rmtree(folder)
    persistence.remove(status_path(catalog_id))


async def discard(catalog_id: str) -> None:
    """Remove this node's copy and sync state of a catalog that is no longer a record."""
    task = _tasks.pop(catalog_id, None)
    if task and not task.done():
        task.cancel()
    state.proot_status.pop(catalog_id, None)
    try:
        await asyncio.to_thread(_remove_content, catalog_id)
    except Exception as exc:  # noqa: BLE001 - the next reconcile tries again
        logger.warning("Could not remove the content of the deleted catalog '%s': %s", catalog_id, exc)
        return
    logger.info("Removed the content of the deleted PRoot Apps catalog '%s'.", catalog_id)


async def auto_update() -> None:
    """Sync every catalog that asks to be kept current; the update job calls this."""
    for catalog in list(state.proot_catalogs.values()):
        if catalog.auto_update:
            await sync(catalog)


async def wait_for_syncs() -> None:
    """Stop the running syncs; the server's shutdown calls this."""
    _stopping.set()
    pending = [task for task in _tasks.values() if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
