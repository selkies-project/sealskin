"""FastAPI application factory and lifespan.

The application object is created here and every router is registered. State
initialisation, cache refreshes, the configuration file watcher and the
background jobs live in `lifespan`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from docker.errors import DockerException
from fastapi import FastAPI

from . import collaboration, config_store, persistence, user_manager
from .docker_utils import (
    container_exists,
    detect_gpus,
    get_and_cache_image_metadata,
    inspect_self_container,
    prune_dangling_images,
    pull_and_cache_image,
    read_cpu_model,
)
from .routers import (
    admin,
    applications,
    files,
    handshake,
    homedirs,
    internal,
    launch,
    sessions,
    shares,
    ui,
    uploads,
)
from .security import init_server_keys, proxy_cert_not_after, prune_crypto_sessions
from .settings import settings
from .state import state
from .version import __version__

logger = logging.getLogger(__name__)

init_server_keys()


async def _remove_stale_sessions() -> None:
    """Drop persisted sessions whose containers no longer exist."""
    if not state.sessions:
        return
    logger.info("Checking for stale sessions from persistence file...")
    stale: list[str] = []
    try:
        for session_id, data in list(state.sessions.items()):
            instance_id = data.get("instance_id")
            if not instance_id or not await container_exists(instance_id):
                stale.append(session_id)
    except (DockerException, RuntimeError) as exc:
        logger.error("Could not connect to Docker to clean up stale sessions: %s", exc)
        return
    if stale:
        logger.info("Found %d stale session(s) to remove.", len(stale))
        async with state.sessions_lock:
            for session_id in stale:
                state.sessions.pop(session_id, None)
        await config_store.save_sessions()


async def background_update_job() -> None:
    """Periodically refresh store caches, pull images and prune dangling ones."""
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
        await prune_dangling_images()


async def background_share_cleanup_job() -> None:
    """Periodically delete expired public shares."""
    while True:
        await asyncio.sleep(settings.share_cleanup_interval_seconds)
        await shares.cleanup_expired_shares()


async def _reload_installed_apps(_path: str) -> None:
    """Watcher callback: reload installed apps after an external edit."""
    logger.info("installed_apps.yml changed on disk; reloading.")
    config_store.load_installed_apps()


async def _reload_app_stores(_path: str) -> None:
    """Watcher callback: reload app stores after an external edit."""
    logger.info("app_stores.yml changed on disk; reloading.")
    config_store.load_app_stores()
    config_store.load_store_entries()
    config_store.resolve_all_apps()


async def _reload_templates(_path: str) -> None:
    """Watcher callback: reload templates after an external edit."""
    logger.info("App templates changed on disk; reloading.")
    config_store.load_app_templates()


async def _reload_users(_path: str) -> None:
    """Watcher callback: reload users and groups after an external edit."""
    logger.info("Users or groups changed on disk; reloading.")
    user_manager.load_users_and_groups()


def _watch_targets() -> dict[str, persistence.ReloadCallback]:
    """Return the configuration paths to watch and their reload callbacks."""
    return {
        settings.installed_apps_path: _reload_installed_apps,
        settings.app_stores_path: _reload_app_stores,
        settings.app_templates_path: _reload_templates,
        os.path.join(settings.keys_base_path, "users"): _reload_users,
        os.path.join(settings.keys_base_path, "admins"): _reload_users,
        settings.groups_base_path: _reload_users,
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
    ):
        os.makedirs(path, exist_ok=True, mode=mode)

    _warn_if_cert_expiring()
    config_store.load_public_shares()
    await config_store.load_sessions()
    await _remove_stale_sessions()

    await inspect_self_container()
    read_cpu_model()
    user_manager.set_external_ports(state.discovered_api_port, state.discovered_session_port)
    user_manager.load_users_and_groups()

    config_store.load_app_stores()
    config_store.load_app_templates()
    detect_gpus()

    logger.info("Populating app store cache...")
    await config_store.refresh_store_caches()
    logger.info("Performing initial population of autostart script cache...")
    await config_store.refresh_autostart_caches()
    config_store.load_installed_apps()

    logger.info("Populating initial image metadata cache...")
    for image_name in {app.provider_config.image for app in state.installed_apps.values()}:
        await get_and_cache_image_metadata(image_name)
    logger.info("Image metadata cache populated.")

    tasks: list[asyncio.Task] = []
    stop_event = asyncio.Event()
    if settings.auto_update_apps:
        tasks.append(asyncio.create_task(background_update_job()))
    tasks.append(asyncio.create_task(background_share_cleanup_job()))
    if settings.watch_config_files:
        tasks.append(asyncio.create_task(persistence.watch_paths(_watch_targets(), stop_event)))
    try:
        yield
    finally:
        logger.info("API server shutting down...")
        stop_event.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Background tasks stopped.")


def create_app() -> FastAPI:
    """Build the FastAPI application with every router registered.

    Returns:
        The configured `FastAPI` instance.
    """
    app = FastAPI(title="SealSkin API", version=__version__, lifespan=lifespan)

    app.include_router(handshake.router)
    app.include_router(applications.router)
    app.include_router(launch.router)
    app.include_router(admin.status_router)
    app.include_router(admin.router)
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
    ui.mount_ui(app)
    return app


api_app = create_app()
