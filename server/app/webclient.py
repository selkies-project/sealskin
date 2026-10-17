"""The web client of an application image, exported once per image and served by this node.

A session's page is the Selkies web client. Served by the container, it is
code of the image running on whatever origin the browser reached it on, and
on the web app's origin that code could act as the signed-in user. So the
node takes the client out of the image instead, once per image digest, with
a throwaway instance of the image that gives up `web_client_path` as a tar
stream (`BaseProvider.export_web_client`), and serves it at `/<session id>/`
itself; the container answers its `api/` alone. The export runs after every
pull and, failing that, at the first launch of the image on this node.

Bundles live under `<node_state_path>/web/<digest>/`, one subdirectory per
dashboard as the image keeps them; `dashboard_dir` picks the one a session's
`DASHBOARD` names. `prune` removes the bundles of images no installed
application runs any more.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tarfile
import tempfile
from typing import Any

from fastapi import HTTPException

from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

DEFAULT_DASHBOARD = "selkies-dashboard"
#: Bytes an export may be at most, unpacked: a dashboard is a few megabytes.
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
#: Seconds an export of one image may take, the throwaway instance's start included.
EXPORT_TIMEOUT = 300

_locks: dict[str, asyncio.Lock] = {}


def bundles_root() -> str:
    """Return the directory the exported clients are kept under."""
    return os.path.join(settings.node_state_path, "web")


def bundle_dir(digest: str) -> str:
    """Return the directory of an image's exported client, by its digest."""
    return os.path.join(bundles_root(), digest.split(":")[-1])


def dashboard_dir(bundle: str, env: dict[str, Any] | None) -> str | None:
    """Return the directory of the dashboard a session serves, inside a bundle.

    Args:
        bundle: The image's bundle directory.
        env: The session's environment, whose `DASHBOARD` names the dashboard.

    Returns:
        The directory holding `index.html`: the named dashboard, the default
        one, any other, or the bundle itself for an image that keeps one
        client at `web_client_path`; `None` when the bundle holds no client.
    """
    wanted = str((env or {}).get("DASHBOARD") or "").strip() or DEFAULT_DASHBOARD
    candidates = [wanted, DEFAULT_DASHBOARD]
    try:
        candidates += sorted(name for name in os.listdir(bundle) if name not in candidates)
    except OSError:
        return None
    for name in candidates:
        if name.startswith(".") or "/" in name:
            continue
        path = os.path.join(bundle, name)
        if os.path.isfile(os.path.join(path, "index.html")):
            return path
    return bundle if os.path.isfile(os.path.join(bundle, "index.html")) else None


def _unpack(data: bytes, target: str, top: str) -> None:
    """Unpack an export into `target`, refusing anything that would land outside it.

    Args:
        data: The tar stream, gzipped or not.
        target: The directory to fill.
        top: The name of the exported directory: entries under it, as a copy
            of the directory itself has them, lose that first component.
    """
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        members = []
        for member in archive.getmembers():
            name = os.path.normpath(member.name)
            if name.startswith(("/", "..")) or os.path.isabs(member.name):
                raise ValueError(f"The export names a path outside the bundle: {member.name!r}.")
            if top and (name == top or name.startswith(top + os.sep)):
                if name == top:
                    continue
                member.name = name = name[len(top) + 1 :]
            if member.issym() or member.islnk():
                linked = os.path.normpath(os.path.join(os.path.dirname(name), member.linkname))
                if linked.startswith("..") or os.path.isabs(member.linkname):
                    raise ValueError(f"The export links outside the bundle: {member.name!r}.")
            elif not (member.isfile() or member.isdir()):
                continue
            total += member.size
            if total > MAX_BUNDLE_BYTES:
                raise ValueError("The export is larger than a web client can be.")
            members.append(member)
        archive.extractall(target, members=members, filter="data")


async def _export(image: str, digest: str) -> str:
    """Export an image's client into its bundle directory and return the directory."""
    from .providers import get_provider

    target = bundle_dir(digest)
    os.makedirs(bundles_root(), exist_ok=True, mode=0o700)
    data = await asyncio.wait_for(get_provider().export_web_client(image, settings.web_client_path), EXPORT_TIMEOUT)
    staging = tempfile.mkdtemp(prefix=".export-", dir=bundles_root())
    try:
        await asyncio.to_thread(_unpack, data, staging, os.path.basename(settings.web_client_path.rstrip("/")))
        if await asyncio.to_thread(dashboard_dir, staging, None) is None:
            raise ValueError(f"'{settings.web_client_path}' in the image holds no web client (no index.html).")
        try:
            os.rename(staging, target)
        except OSError:
            # Another export of the same image got in first.
            if not os.path.isdir(target):
                raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    logger.info("Exported the web client of '%s' (%s).", image, digest[:19])
    return target


async def _digest(image: str) -> str | None:
    """Return the digest of the image sessions of `image` run now, pulling or pinning it when none is known."""
    from .providers import get_provider

    provider = get_provider()
    info = await provider.get_local_image_info(image)
    if not info:
        await provider.pull_image(image)
        info = await provider.get_local_image_info(image)
    return info["id"] if info else None


async def ensure(image: str) -> str:
    """Return the bundle directory of an image's client, exporting it when this node has none.

    Args:
        image: The image reference an application runs.

    Returns:
        The bundle directory.

    Raises:
        RuntimeError: When the image has no digest, or the export fails.
    """
    digest = await _digest(image)
    if not digest:
        raise RuntimeError(f"The digest of image '{image}' is unknown; pull it first.")
    target = bundle_dir(digest)
    if os.path.isdir(target):
        return target
    lock = _locks.setdefault(digest, asyncio.Lock())
    async with lock:
        if os.path.isdir(target):
            return target
        try:
            return await _export(image, digest)
        except Exception as exc:
            raise RuntimeError(f"The web client of image '{image}' could not be exported: {exc}") from exc


async def export_quietly(image: str) -> None:
    """Export an image's client after a pull, logging a failure rather than raising it."""
    try:
        await ensure(image)
    except Exception as exc:  # noqa: BLE001 - the launch tries again
        logger.warning("%s", exc)


def prune(keep: set[str]) -> None:
    """Remove the bundles of every digest not in `keep`.

    Args:
        keep: The digests of the images installed applications run, and of
            the sessions running.
    """
    root = bundles_root()
    wanted = {digest.split(":")[-1] for digest in keep if digest}
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        if name in wanted or name.startswith("."):
            continue
        shutil.rmtree(os.path.join(root, name), ignore_errors=True)
        logger.info("Removed the exported web client %s, which no image uses.", name)


def digests_in_use() -> set[str]:
    """Return the digests of the installed applications' images and the running sessions' bundles."""
    digests = {
        str(entry.get("id") or entry.get("pinned") or "")
        for entry in state.image_metadata.values()
    }
    for session in state.sessions.values():
        root = str(session.get("web_root") or "")
        if root.startswith(bundles_root() + os.sep):
            digests.add(root[len(bundles_root()) + 1 :].split(os.sep)[0])
    return {digest for digest in digests if digest}


async def root_for_session(app_image: str, env: dict[str, Any], required: bool) -> str | None:
    """Return the directory a new session's client is served from.

    Args:
        app_image: The image the session runs.
        env: The session's environment, for its `DASHBOARD`.
        required: Whether the session cannot be served without it: a web
            sign-in's session on the web app's origin.

    Returns:
        The dashboard directory, or `None` when the client could not be
        exported and the session may be served by its container.

    Raises:
        HTTPException: 500 when the client is required and cannot be had.
    """
    try:
        bundle = await ensure(app_image)
        root = await asyncio.to_thread(dashboard_dir, bundle, env)
        if root is None:
            raise RuntimeError(f"The exported client of image '{app_image}' holds no dashboard.")
        return root
    except RuntimeError as exc:
        if required:
            raise HTTPException(
                status_code=500,
                detail=f"{exc} A session of a web sign-in is served by this node's copy of the application's web client; "
                "see SEALSKIN_WEB_CLIENT_PATH.",
            ) from exc
        logger.warning("%s The container serves its own web client.", exc)
        return None
