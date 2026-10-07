"""FastAPI application factory and lifespan.

The application object is created here and every router is registered. State
initialisation, cache refreshes, the configuration file watcher, and the
background jobs (image updates, share expiry, and session reconciliation)
live in `lifespan`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import (
    cluster,
    collaboration,
    config_store,
    filesync,
    persistence,
    prootapps,
    quota,
    routing,
    sso,
    store,
    user_manager,
    webclient,
)
from .docker_utils import get_and_cache_image_metadata, pull_and_cache_image, read_cpu_model
from .launch import reconcile_sessions
from .providers import get_provider
from .routers import (
    admin,
    applications,
    cluster_admin,
    entry,
    files,
    handshake,
    homedirs,
    internal,
    launch,
    peer,
    sessions,
    shares,
    ui,
    uploads,
)
from .routers import sso as sso_routes
from .security import init_server_keys, proxy_cert_not_after, prune_crypto_sessions
from .settings import settings
from .state import state
from .version import __version__

logger = logging.getLogger(__name__)

init_server_keys()


#: Seconds between two passes of `reconcile_sessions`.
RECONCILE_INTERVAL = 30


async def background_reconcile_job() -> None:
    """Periodically reconcile sessions with the backend."""
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL)
        await reconcile_sessions()


async def background_update_job() -> None:
    """Periodically refresh store caches, pull images, and prune dangling ones."""
    while True:
        await asyncio.sleep(settings.auto_update_interval_seconds)
        prune_crypto_sessions()
        await config_store.refresh_store_caches()
        await config_store.refresh_autostart_caches()
        config_store.resolve_all_apps()

        logger.info("Starting scheduled app image update check...")
        images = {
            app.provider_config.image for app in state.installed_apps.values() if app.auto_update
        }
        for image_name in images:
            await pull_and_cache_image(image_name)
            await asyncio.sleep(2)

        logger.info("Cleaning up dangling images...")
        await get_provider().prune_images()
        await asyncio.to_thread(webclient.prune, webclient.digests_in_use())
        await prootapps.auto_update()


async def background_share_cleanup_job() -> None:
    """Periodically delete expired public shares."""
    while True:
        await asyncio.sleep(settings.share_cleanup_interval_seconds)
        await shares.cleanup_expired_shares()


async def _reload_installed_apps(_path: str) -> None:
    """Watcher callback: reload installed apps after an external edit."""
    logger.info("installed_apps.yml changed on disk; reloading.")
    await asyncio.to_thread(config_store.load_installed_apps)


async def _reload_app_stores(_path: str) -> None:
    """Watcher callback: reload app stores after an external edit."""
    logger.info("app_stores.yml changed on disk; reloading.")
    config_store.load_app_stores()
    config_store.load_store_entries()
    config_store.resolve_all_apps()


async def _reload_templates(_path: str) -> None:
    """Watcher callback: reload templates after an external edit."""
    logger.info("App templates changed on disk; reloading.")
    await asyncio.to_thread(config_store.load_app_templates)


async def _reload_proot_catalogs(_path: str) -> None:
    """Watcher callback: reload the PRoot Apps catalogs and bring this node's copies along."""
    logger.info("PRoot Apps catalogs changed; reloading.")
    await asyncio.to_thread(prootapps.load_catalogs)
    prootapps.reconcile()


async def _reload_cluster(_path: str) -> None:
    """Watcher callback: reload the cluster's records after another node or an administrator changed them."""
    logger.info("Cluster records changed; reloading.")
    await asyncio.to_thread(cluster.apply_registry)
    await asyncio.to_thread(quota.load)


_records_pending = False


def _sync_records() -> None:
    """Bring this node and the shared records together: the root token, its own record, and the registry."""
    sso.ensure_root_token()
    cluster.register_self()
    cluster.apply_registry()
    cluster.adopt_local_homes()
    quota.load()


def _catch_up() -> None:
    """Finish what a start without the store left undone, and read again everything it served from its copy."""
    _sync_records()
    user_manager.load_users_and_groups()
    config_store.load_app_stores()
    config_store.load_store_entries()
    config_store.load_app_templates()
    config_store.load_installed_apps()
    prootapps.load_catalogs()


async def background_peer_job() -> None:
    """Ask the other nodes for their sessions and load, over and over."""
    global _records_pending
    while True:
        if _records_pending:
            try:
                await asyncio.to_thread(_catch_up)
                _records_pending = False
                logger.info("The shared records answer again; this node caught up with them.")
            except store.StoreUnavailable:
                pass
            except Exception as exc:  # noqa: BLE001 - try again next round
                logger.warning("Could not catch up with the shared records: %s", exc)
        try:
            await cluster.refresh_peers()
        except Exception as exc:  # noqa: BLE001 - keep asking
            logger.warning("Could not ask the other nodes for their status: %s", exc)
        await asyncio.sleep(settings.peer_poll_seconds)


async def background_usage_job() -> None:
    """Write this node's usage and sync the shared files of its running sessions, over and over."""
    while True:
        await asyncio.sleep(settings.usage_flush_seconds)
        await quota.flush()
        shared = {
            (data["username"], data["shared_files_path"])
            for data in state.sessions.values()
            if data.get("username")
            and data.get("shared_files_path")
            and not data["shared_files_path"].startswith(os.path.join(settings.storage_path, "sealskin_ephemeral"))
        }
        for username, path in shared:
            await filesync.sync(username, path)


async def _reload_users(_path: str) -> None:
    """Watcher callback: reload users and groups after an external edit."""
    logger.info("Users or groups changed on disk; reloading.")
    await asyncio.to_thread(user_manager.load_users_and_groups)


def _watch_targets() -> dict[str, persistence.ReloadCallback]:
    """Return the configuration paths to watch and their reload callbacks."""
    return {
        settings.installed_apps_path: _reload_installed_apps,
        settings.app_stores_path: _reload_app_stores,
        settings.app_templates_path: _reload_templates,
        settings.proot_catalogs_path: _reload_proot_catalogs,
        os.path.join(settings.keys_base_path, "users"): _reload_users,
        os.path.join(settings.keys_base_path, "admins"): _reload_users,
        settings.groups_base_path: _reload_users,
        settings.cluster_path: _reload_cluster,
    }


def _warn_if_cert_expiring(days: int = 14) -> None:
    """Log a warning when the proxy TLS certificate is expired or about to expire."""
    expires_at = proxy_cert_not_after(settings.proxy_cert_path)
    if expires_at is None:
        return
    remaining_days = (expires_at - time.time()) / 86400
    if remaining_days < 0:
        logger.error(
            "The proxy TLS certificate at %s EXPIRED %.0f day(s) ago. HTTPS clients will fail "
            "with 'Failed to fetch' until it is renewed.",
            settings.proxy_cert_path,
            -remaining_days,
        )
    elif remaining_days < days:
        logger.warning(
            "The proxy TLS certificate at %s expires in %.0f day(s).",
            settings.proxy_cert_path,
            remaining_days,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise state on startup and stop background tasks on shutdown."""
    logger.info("SealSkin API server %s starting up...", __version__)
    for path, mode in (
        (settings.upload_dir, 0o700),
        (settings.app_icons_path, 0o700),
        (settings.autostart_cache_path, 0o700),
        (settings.app_store_cache_path, 0o700),
        (settings.storage_path, 0o755),
        (settings.home_templates_path, 0o700),
        (os.path.join(settings.storage_path, "sealskin_ephemeral"), 0o700),
        (settings.public_storage_path, 0o700),
        (settings.node_state_path, 0o700),
    ):
        os.makedirs(path, exist_ok=True, mode=mode)

    cluster.init()
    await asyncio.to_thread(cluster.join)
    if store.is_local():
        os.makedirs(settings.cluster_path, exist_ok=True, mode=0o700)
    logger.info("Node %s ('%s'), roles: %s. Shared records: %s.", cluster.NODE_ID, cluster.node_name(), ", ".join(sorted(cluster.roles())), store.describe()["kind"])

    _warn_if_cert_expiring()
    config_store.load_public_shares()
    await config_store.load_sessions()

    provider = get_provider()
    await provider.inspect_self()
    await reconcile_sessions()
    read_cpu_model()
    _, external_port = user_manager.external_address()
    if external_port:
        state.discovered_api_port = state.discovered_session_port = external_port
    user_manager.set_external_ports(state.discovered_api_port, state.discovered_session_port)
    user_manager.load_users_and_groups()
    sso.load()
    global _records_pending
    try:
        _sync_records()
    except store.StoreUnavailable as exc:
        logger.error("%s Starting on this node's copy of the shared records; nothing can be changed until it answers.", exc)
        _records_pending = True
        cluster.load_registry()
    if not cluster.is_approved():
        logger.warning(
            "This node is not an approved member of the cluster yet: an administrator approves node %s in the dashboard.",
            cluster.NODE_ID,
        )

    config_store.load_app_stores()
    config_store.load_app_templates()
    await provider.detect_gpus()

    logger.info("Populating app store cache...")
    await config_store.refresh_store_caches()
    logger.info("Performing initial population of autostart script cache...")
    await config_store.refresh_autostart_caches()
    config_store.load_installed_apps()
    prootapps.load_catalogs()
    prootapps.reconcile()

    logger.info("Populating initial image metadata cache...")
    for image_name in {app.provider_config.image for app in state.installed_apps.values()}:
        await get_and_cache_image_metadata(image_name)
    logger.info("Image metadata cache populated.")

    tasks: list[asyncio.Task] = []
    stop_event = asyncio.Event()
    if settings.auto_update_apps:
        tasks.append(asyncio.create_task(background_update_job()))
    tasks.append(asyncio.create_task(background_share_cleanup_job()))
    tasks.append(asyncio.create_task(background_reconcile_job()))
    tasks.append(asyncio.create_task(background_peer_job()))
    tasks.append(asyncio.create_task(background_usage_job()))
    if not store.is_local():
        tasks.append(
            asyncio.create_task(
                persistence.watch_store(
                    _watch_targets(), stop_event, settings.store_poll_seconds, cluster.store_wake
                )
            )
        )
    elif settings.watch_config_files:
        tasks.append(asyncio.create_task(persistence.watch_paths(_watch_targets(), stop_event)))
    try:
        yield
    finally:
        logger.info("API server shutting down...")
        stop_event.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await prootapps.wait_for_syncs()
        quota.tick()
        await quota.flush()
        logger.info("Background tasks stopped.")


async def _conflict(_request: Request, _exc: Exception) -> JSONResponse:
    """Answer a write another node got in ahead of, after looking at what it wrote."""
    cluster.store_wake.set()
    return JSONResponse(status_code=409, content={"detail": "Another node changed this record. Reload and try again."})


async def _unavailable(_request: Request, exc: Exception) -> JSONResponse:
    """Answer a write the store could not take."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def create_app() -> FastAPI:
    """Build the FastAPI application with every router registered.

    Returns:
        The configured `FastAPI` instance.
    """
    app = FastAPI(title="SealSkin API", version=__version__, lifespan=lifespan)

    app.include_router(handshake.router)
    app.include_router(applications.router)
    app.include_router(launch.router)
    app.include_router(launch.progress_router)
    app.include_router(admin.status_router)
    app.include_router(admin.router)
    app.include_router(admin.template_router)
    app.include_router(homedirs.router)
    app.include_router(sessions.router)
    app.include_router(uploads.router)
    app.include_router(files.router)
    app.include_router(shares.router)
    app.include_router(collaboration.router)
    app.include_router(internal.router)
    app.include_router(sessions.proxy_router)
    app.include_router(shares.public_router)
    app.include_router(ui.router)
    app.include_router(entry.router)
    app.include_router(sso_routes.router)
    app.include_router(peer.router)
    app.include_router(cluster_admin.router)
    app.include_router(cluster_admin.user_router)
    app.add_exception_handler(cluster.Forwarded, routing.forwarded_response)
    app.add_exception_handler(store.Conflict, _conflict)
    app.add_exception_handler(store.StoreUnavailable, _unavailable)
    ui.mount_ui(app)
    return app


api_app = create_app()
