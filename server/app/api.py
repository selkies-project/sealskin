import base64
import json
import os
import shutil
import uuid
import hashlib
import time
import secrets
import yaml
import logging
import pathlib
import subprocess
import tempfile
import re
from typing import Callable, Dict, List, Optional
import asyncio
from contextlib import asynccontextmanager
import httpx
from collections import defaultdict
import docker
from docker.errors import DockerException, NotFound

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Depends, HTTPException, Request, Response, APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.routing import APIRoute
from jose import JWTError, jwt
from pydantic import ValidationError

from .settings import settings
from .models import *
from .providers.docker_provider import DockerProvider
from . import user_manager

logger = logging.getLogger(__name__)

SESSIONS_LOCK = asyncio.Lock()
SESSIONS_DB: Dict[str, Dict] = {}
CRYPTO_SESSIONS: Dict[str, bytes] = {}
INSTALLED_APPS: Dict[str, InstalledApp] = {}
APP_STORES: List[AppStore] = []
APP_TEMPLATES: Dict[str, Dict] = {}
PROVIDER_CACHE: Dict[str, DockerProvider] = {}
AVAILABLE_GPUS: List[Dict] = []
PUBLIC_SHARES_METADATA: Dict[str, PublicShareMetadata] = {}
IMAGE_METADATA: Dict[str, Dict] = {}
DELETION_TASKS: Dict[str, Dict] = {}
PULL_STATUS: Dict[str, str] = {}
SYSTEM_STATS_CACHE: Dict[str, any] = {"data": None, "timestamp": 0}
CPU_MODEL: str = "Unknown"
PATH_PREFIX_MAP: Dict[str, str] = {}
DISCOVERED_API_PORT: int = settings.api_port
DISCOVERED_SESSION_PORT: int = settings.session_port
METADATA_LOCK = asyncio.Lock()

try:
    with open(settings.server_private_key_path, "rb") as f:
        SERVER_PRIVATE_KEY = serialization.load_pem_private_key(f.read(), password=None)
    SERVER_PUBLIC_KEY_PEM = (
        SERVER_PRIVATE_KEY.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    user_manager.set_server_public_key(SERVER_PUBLIC_KEY_PEM)
except FileNotFoundError as e:
    logger.error(f"Key file not found: {e.filename}. Exiting.")
    exit(1)


ALGORITHM = "RS256"

async def save_sessions_to_disk():
    """Saves the current session database to a file."""
    async with SESSIONS_LOCK:
        try:
            os.makedirs(os.path.dirname(settings.sessions_db_path), exist_ok=True)
            with tempfile.NamedTemporaryFile('w', delete=False, dir=os.path.dirname(settings.sessions_db_path)) as tf:
                yaml.dump(SESSIONS_DB, tf, sort_keys=False)
                temp_path = tf.name
            shutil.move(temp_path, settings.sessions_db_path)
            logger.info(f"Successfully saved {len(SESSIONS_DB)} session(s) to disk.")
        except Exception as e:
            logger.error(f"Failed to save sessions to disk: {e}")

async def load_sessions_from_disk():
    """Loads the session database from a file if it exists."""
    global SESSIONS_DB
    if not os.path.exists(settings.sessions_db_path):
        logger.info("Session persistence file not found. Starting with an empty session database.")
        return

    async with SESSIONS_LOCK:
        try:
            with open(settings.sessions_db_path, "r") as f:
                loaded_sessions = yaml.safe_load(f) or {}
                SESSIONS_DB.update(loaded_sessions)
                logger.info(f"Loaded {len(loaded_sessions)} session(s) from disk.")
        except Exception as e:
            logger.error(f"Failed to load sessions from disk: {e}")


async def _fetch_and_cache_single_script(store_name: str, base_url: str, app_id: str):
    """Fetches and caches a single autostart script, respecting ETags."""
    cache_dir = os.path.join(settings.autostart_cache_path, store_name)
    os.makedirs(cache_dir, exist_ok=True)
    cache_file_path = os.path.join(cache_dir, app_id)
    meta_file_path = cache_file_path + ".meta"
    headers = {}

    if os.path.exists(meta_file_path):
        try:
            with open(meta_file_path, "r") as f:
                meta = json.load(f)
                if "etag" in meta:
                    headers["If-None-Match"] = meta["etag"]
        except (json.JSONDecodeError, IOError):
            logger.warning(f"Could not read meta file for {app_id}")

    autostart_url = f"{base_url}/autostart/{app_id}"

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(autostart_url, timeout=10, headers=headers)

            if response.status_code == 304:
                logger.debug(f"Autostart script for '{app_id}' in store '{store_name}' is up to date.")
                return

            if response.status_code == 404:
                with open(cache_file_path, "w") as f: f.write("")
                if os.path.exists(meta_file_path): os.remove(meta_file_path)
                return

            response.raise_for_status()
            script_content = response.text

            def write_cache_and_meta():
                with open(cache_file_path, "w") as f: f.write(script_content)
                if "etag" in response.headers:
                    with open(meta_file_path, "w") as f: json.dump({"etag": response.headers["etag"]}, f)

            await asyncio.to_thread(write_cache_and_meta)
            logger.info(f"Successfully cached autostart script for '{app_id}' from store '{store_name}'.")

    except httpx.RequestError as e:
        logger.warning(f"Failed to fetch autostart script for '{app_id}': {e}")
    except Exception as e:
        logger.error(f"Unexpected error updating autostart script for '{app_id}': {e}")


async def _update_all_autostart_caches():
    """Iterates all app stores and updates the autostart script cache for every available app."""
    logger.info("Starting autostart script cache refresh for all app stores...")
    tasks = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for store in APP_STORES:
            try:
                response = await client.get(store.url, timeout=15)
                response.raise_for_status()
                data = yaml.safe_load(response.text)
                
                apps_list = data['apps'] if isinstance(data, dict) and 'apps' in data else data
                if not isinstance(apps_list, list):
                    logger.error(f"Could not find a list of apps in store '{store.name}'")
                    continue

                base_url = store.url.rsplit("/", 1)[0]
                for app in apps_list:
                    if app.get("provider_config", {}).get("autostart"):
                        tasks.append(
                            _fetch_and_cache_single_script(store.name, base_url, app["id"])
                        )
            except Exception as e:
                logger.error(f"Failed to process app store '{store.name}' for autostart cache: {e}")
    
    if tasks:
        await asyncio.gather(*tasks)
    logger.info("Autostart script cache refresh complete.")

async def _inspect_self_container():
    """If running in Docker, inspects self to find host mount paths and published ports."""
    global PATH_PREFIX_MAP, DISCOVERED_API_PORT, DISCOVERED_SESSION_PORT
    if not os.path.exists("/var/run/docker.sock"):
        logger.info("Docker socket not found. Assuming running on host.")
        return

    try:
        client = await asyncio.to_thread(docker.from_env)
        containers = await asyncio.to_thread(client.containers.list, filters={'name': 'sealskin'})
        
        if not containers:
            hostname = os.uname()[1]
            logger.info(f"No container named 'sealskin' found. Trying with hostname '{hostname}'.")
            try:
                container = await asyncio.to_thread(client.containers.get, hostname)
                containers = [container]
            except docker.errors.NotFound:
                logger.warning("Could not find self-container by name 'sealskin' or hostname. Path remapping will be disabled.")
                return
        
        container = containers[0]
        logger.info(f"Found self-container '{container.name}'. Inspecting mounts.")
        
        mounts = container.attrs.get("Mounts", [])
        if not mounts:
            logger.info(f"Container '{container.name}' has no volume mounts to map.")
            return

        for mount in mounts:
            host_path = mount.get("Source")
            container_path = mount.get("Destination")
            if host_path and container_path:
                PATH_PREFIX_MAP[container_path] = host_path
        
        if PATH_PREFIX_MAP:
            logger.info(f"Detected container mount prefixes: {PATH_PREFIX_MAP}")
        else:
            logger.warning("Could not find any usable mount points on the current container.")

    except DockerException as e:
        logger.warning(f"Could not inspect self in Docker. Path mapping disabled. Error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during Docker self-inspection: {e}")

    ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
    if not ports:
        logger.info(f"Container '{container.name}' has no port mappings to inspect.")
    else:
        api_internal = f"{settings.api_port}/tcp"
        session_internal = f"{settings.session_port}/tcp"

        if api_internal in ports and ports[api_internal]:
            if host_port := ports[api_internal][0].get("HostPort"):
                DISCOVERED_API_PORT = int(host_port)
                logger.info(f"Discovered external API port mapping: {settings.api_port} -> {DISCOVERED_API_PORT}")

        if session_internal in ports and ports[session_internal]:
            if host_port := ports[session_internal][0].get("HostPort"):
                DISCOVERED_SESSION_PORT = int(host_port)
                logger.info(f"Discovered external Session port mapping: {settings.session_port} -> {DISCOVERED_SESSION_PORT}")

def _translate_path_to_host(internal_path: str) -> str:
    """Translates a path inside this container to the corresponding host path."""
    if not PATH_PREFIX_MAP or not internal_path:
        return internal_path

    sorted_prefixes = sorted(PATH_PREFIX_MAP.keys(), key=len, reverse=True)

    for container_prefix in sorted_prefixes:
        if internal_path == container_prefix or internal_path.startswith(container_prefix + '/'):
            host_prefix = PATH_PREFIX_MAP[container_prefix]
            
            relative_path = os.path.relpath(internal_path, container_prefix)
            
            if relative_path == ".":
                return host_prefix
            
            translated_path = os.path.join(host_prefix, relative_path)
            logger.debug(f"Translated path '{internal_path}' -> '{translated_path}'")
            return translated_path

    return internal_path

def _get_cpu_model():
    """Reads the CPU model from /proc/cpuinfo and stores it."""
    global CPU_MODEL
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if "model name" in line:
                    CPU_MODEL = line.split(":", 1)[1].strip()
                    logger.info(f"Detected CPU Model: {CPU_MODEL}")
                    break
    except Exception as e:
        logger.warning(f"Could not read CPU model from /proc/cpuinfo: {e}")


def _get_system_stats() -> Dict:
    """Gets cached or fresh system stats (disk usage, CPU model)."""
    now = time.time()
    if SYSTEM_STATS_CACHE["data"] and (now - SYSTEM_STATS_CACHE["timestamp"] < 60):
        return SYSTEM_STATS_CACHE["data"]

    try:
        usage = shutil.disk_usage(settings.storage_path)
        stats = {
            "cpu_model": CPU_MODEL,
            "disk_total": usage.total,
            "disk_used": usage.used,
        }
        SYSTEM_STATS_CACHE["data"] = stats
        SYSTEM_STATS_CACHE["timestamp"] = now
        return stats
    except Exception as e:
        logger.error(f"Failed to get system stats: {e}")
        return {"cpu_model": CPU_MODEL, "disk_total": None, "disk_used": None}


def detect_gpus():
    """Detects available GPUs on the host and populates AVAILABLE_GPUS."""
    global AVAILABLE_GPUS
    AVAILABLE_GPUS.clear()
    cmd = "ls -la /sys/class/drm/renderD*/device/driver 2>/dev/null | awk '{print $11}' | awk -F/ '{print $NF}'"
    try:
        result = subprocess.run(
            cmd, shell=True, check=True, capture_output=True, text=True
        )
        drivers = result.stdout.strip().split("\n")

        render_devices = sorted(
            [f for f in os.listdir("/sys/class/drm") if f.startswith("renderD")],
            key=lambda x: int(x.replace("renderD", "")),
        )

        if len(drivers) != len(render_devices):
            logger.warning(
                f"Mismatch between detected drivers ({len(drivers)}) and render devices ({len(render_devices)}). GPU detection might be inaccurate."
            )
            return

        nvidia_index = 0
        for i, driver in enumerate(drivers):
            if not driver:
                continue
            device_name = render_devices[i]
            device_path = f"/dev/dri/{device_name}"
            gpu_info = {"device": device_path, "driver": driver}
            if driver == "nvidia":
                gpu_info["type"] = "nvidia"
                gpu_info["index"] = nvidia_index
                nvidia_index += 1
            else:
                gpu_info["type"] = "dri3"

            AVAILABLE_GPUS.append(gpu_info)

        logger.info(f"Detected {len(AVAILABLE_GPUS)} GPU(s): {AVAILABLE_GPUS}")

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.info(
            f"GPU detection command failed or could not be run: {e}. No GPUs will be available."
        )
    except Exception as e:
        logger.error(f"An unexpected error occurred during GPU detection: {e}")


def _ensure_config_dir():
    config_dir = os.path.dirname(settings.installed_apps_path)
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(settings.app_templates_path, exist_ok=True)


def load_app_templates():
    global APP_TEMPLATES
    APP_TEMPLATES.clear()

    if os.path.isdir(settings.default_app_templates_path):
        for filename in os.listdir(settings.default_app_templates_path):
            if filename.endswith((".yml", ".yaml")):
                try:
                    with open(
                        os.path.join(settings.default_app_templates_path, filename), "r"
                    ) as f:
                        template_data = yaml.safe_load(f)
                        template_name = template_data.get("name")
                        if template_name:
                            APP_TEMPLATES[template_name] = template_data
                except Exception as e:
                    logger.error(f"Error loading default template {filename}: {e}")

    if os.path.isdir(settings.app_templates_path):
        for filename in os.listdir(settings.app_templates_path):
            if filename.endswith((".yml", ".yaml")):
                try:
                    with open(
                        os.path.join(settings.app_templates_path, filename), "r"
                    ) as f:
                        template_data = yaml.safe_load(f)
                        template_name = template_data.get("name")
                        if template_name:
                            APP_TEMPLATES[template_name] = template_data
                except Exception as e:
                    logger.error(f"Error loading user template {filename}: {e}")

    if not APP_TEMPLATES:
        logger.warning("No app templates found. Creating a blank 'Default' template.")
        default_template = {"name": "Default", "settings": {}}
        APP_TEMPLATES["Default"] = default_template
        try:
            os.makedirs(settings.app_templates_path, exist_ok=True)
            with open(
                os.path.join(settings.app_templates_path, "default.yml"), "w"
            ) as f:
                yaml.dump(default_template, f)
        except Exception as e:
            logger.error(f"Could not write default template file: {e}")

    logger.info(f"Loaded {len(APP_TEMPLATES)} application template(s).")


def load_app_configs():
    global INSTALLED_APPS, APP_STORES
    _ensure_config_dir()

    try:
        if os.path.exists(settings.installed_apps_path):
            with open(settings.installed_apps_path, "r") as f:
                apps_data = yaml.safe_load(f) or []
                INSTALLED_APPS = {app["id"]: InstalledApp(**app) for app in apps_data}
        else:
            INSTALLED_APPS = {}
        logger.info(f"Loaded {len(INSTALLED_APPS)} installed application(s).")
    except (IOError, yaml.YAMLError, ValidationError) as e:
        logger.error(f"Error loading installed apps config: {e}")
        INSTALLED_APPS = {}

    try:
        if os.path.exists(settings.app_stores_path):
            with open(settings.app_stores_path, "r") as f:
                stores_data = yaml.safe_load(f) or []
                APP_STORES = [AppStore(**store) for store in stores_data]
        else:
            APP_STORES = [
                AppStore(name="SealSkin Apps", url=settings.app_resource_path)
            ]
            save_app_stores()
        logger.info(f"Loaded {len(APP_STORES)} app store(s).")
    except (IOError, yaml.YAMLError, ValidationError) as e:
        logger.error(f"Error loading app stores config: {e}")
        APP_STORES = []


def save_installed_apps():
    _ensure_config_dir()
    try:
        apps_list = [app.dict() for app in INSTALLED_APPS.values()]
        with open(settings.installed_apps_path, "w") as f:
            yaml.dump(apps_list, f, sort_keys=False)
    except IOError as e:
        logger.error(f"Failed to save installed apps config: {e}")


def save_app_stores():
    _ensure_config_dir()
    try:
        stores_list = [store.dict() for store in APP_STORES]
        with open(settings.app_stores_path, "w") as f:
            yaml.dump(stores_list, f, sort_keys=False)
    except IOError as e:
        logger.error(f"Failed to save app stores config: {e}")


async def _get_and_cache_image_metadata(image_name: str, force_refresh: bool = False):
    """Retrieves local image metadata, caching it for performance."""
    if (
        not force_refresh
        and image_name in IMAGE_METADATA
        and "sha" in IMAGE_METADATA[image_name]
    ):
        return

    provider = DockerProvider({"provider_config": {"image": image_name}})
    info = await provider.get_local_image_info(image_name)

    if image_name not in IMAGE_METADATA:
        IMAGE_METADATA[image_name] = {}

    if info:
        IMAGE_METADATA[image_name]["sha"] = info["short_id"]
        IMAGE_METADATA[image_name]["digests"] = info["digests"]
    else:
        IMAGE_METADATA[image_name]["sha"] = None
        IMAGE_METADATA[image_name]["digests"] = []


def _get_autostart_cache_path(app_config: InstalledApp) -> Optional[str]:
    """Determines the local cache path for an app's autostart script."""
    store = next((s for s in APP_STORES if s.name == app_config.source), None)
    if not store:
        return None

    cache_dir = os.path.join(settings.autostart_cache_path, store.name)
    return os.path.join(cache_dir, app_config.source_app_id)


async def _update_autostart_cache(app_config: InstalledApp):
    """Fetches an autostart script from a remote URL and caches it locally."""
    if not app_config.provider_config.autostart:
        return

    store = next((s for s in APP_STORES if s.name == app_config.source), None)
    if not store:
        logger.error(
            f"Could not find app store named '{app_config.source}' for app '{app_config.name}'. Cannot fetch autostart script."
        )
        return

    cache_file_path = _get_autostart_cache_path(app_config)
    if not cache_file_path:
        return

    os.makedirs(os.path.dirname(cache_file_path), exist_ok=True)

    app_source_url = store.url
    if not (app_source_url.endswith(".yml") or app_source_url.endswith(".yaml")):
        logger.error(
            f"App store URL does not appear to be a YAML file: {app_source_url}. Cannot determine autostart script path."
        )
        return

    base_url = app_source_url.rsplit("/", 1)[0]
    autostart_url = f"{base_url}/autostart/{app_config.source_app_id}"

    logger.info(
        f"Checking for autostart script for '{app_config.source_app_id}' from {autostart_url}"
    )

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(autostart_url, timeout=10)

            if response.status_code == 404:
                logger.warning(
                    f"No autostart script found for '{app_config.source_app_id}' (404 Not Found). Caching empty response."
                )
                with open(cache_file_path, "w") as f:
                    f.write("")
                return

            response.raise_for_status()
            script_content = response.text

            def write_cache():
                with open(cache_file_path, "w") as f:
                    f.write(script_content)

            await asyncio.to_thread(write_cache)
            logger.info(
                f"Successfully cached autostart script for '{app_config.source_app_id}'."
            )

    except httpx.RequestError as e:
        logger.error(
            f"Failed to fetch autostart script for '{app_config.source_app_id}': {e}"
        )
    except Exception as e:
        logger.error(
            f"Unexpected error updating autostart script for '{app_config.source_app_id}': {e}"
        )


async def _pull_and_cache_image(image_name: str):
    """Pulls the latest version of an image and updates the metadata and autostart cache."""
    if PULL_STATUS.get(image_name) == "pulling":
        logger.info(f"Pull for image '{image_name}' is already in progress.")
        return

    PULL_STATUS[image_name] = "pulling"
    try:
        logger.info(f"Starting background pull for image '{image_name}'...")
        provider = DockerProvider({"provider_config": {"image": image_name}})
        await provider.pull_image(image_name)
        await _get_and_cache_image_metadata(image_name, force_refresh=True)

        if image_name in IMAGE_METADATA:
            IMAGE_METADATA[image_name]["last_checked_at"] = time.time()

        logger.info(f"Background pull for '{image_name}' completed successfully.")
    except Exception as e:
        logger.error(f"Background pull for image '{image_name}' failed: {e}")
    finally:
        PULL_STATUS.pop(image_name, None)


async def background_update_job():
    while True:
        await asyncio.sleep(settings.auto_update_interval_seconds)
        
        await _update_all_autostart_caches()

        logger.info("Starting scheduled app image update check...")
        apps_to_update = [app for app in INSTALLED_APPS.values() if app.auto_update]
        images_to_pull = {app.provider_config.image for app in apps_to_update}

        for image_name in images_to_pull:
            await _pull_and_cache_image(image_name)
            await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API server starting up...")
    os.makedirs(settings.upload_dir, exist_ok=True, mode=0o700)
    os.makedirs(settings.autostart_cache_path, exist_ok=True, mode=0o700)
    os.makedirs(settings.storage_path, exist_ok=True, mode=0o755)
    os.makedirs(os.path.join(settings.storage_path, "sealskin_ephemeral"), exist_ok=True, mode=0o700)
    os.makedirs(settings.public_storage_path, exist_ok=True, mode=0o700)
    load_public_shares_metadata()
    await load_sessions_from_disk()

    if SESSIONS_DB:
        logger.info("Checking for stale sessions from persistence file...")
        stale_sessions = []
        try:
            docker_client = await asyncio.to_thread(docker.from_env)
            for session_id, session_data in SESSIONS_DB.items():
                try:
                    await asyncio.to_thread(docker_client.containers.get, session_data["instance_id"])
                except NotFound:
                    stale_sessions.append(session_id)

            if stale_sessions:
                logger.info(f"Found {len(stale_sessions)} stale session(s) to remove.")
                async with SESSIONS_LOCK:
                    for session_id in stale_sessions:
                        del SESSIONS_DB[session_id]
                await save_sessions_to_disk()
        except DockerException as e:
            logger.error(f"Could not connect to Docker to clean up stale sessions: {e}")

    await _inspect_self_container()
    _get_cpu_model()
    user_manager.load_users_and_groups()
    load_app_configs()
    load_app_templates()
    detect_gpus()

    logger.info("Performing initial population of autostart script cache...")
    await _update_all_autostart_caches()
    logger.info("Initial autostart script cache population complete.")

    logger.info("Populating initial image metadata cache...")
    all_images = {app.provider_config.image for app in INSTALLED_APPS.values()}
    for image_name in all_images:
        await _get_and_cache_image_metadata(image_name)
    logger.info("Image metadata cache populated.")

    update_task = None
    cleanup_task = None
    if settings.auto_update_apps:
        update_task = asyncio.create_task(background_update_job())
    cleanup_task = asyncio.create_task(background_share_cleanup_job())
    yield
    logger.info("API server shutting down...")
    if update_task:
        update_task.cancel()
    if cleanup_task:
        cleanup_task.cancel()
    try:
        tasks_to_await = []
        if update_task:
            tasks_to_await.append(update_task)
        if cleanup_task:
            tasks_to_await.append(cleanup_task)
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await)
    except asyncio.CancelledError:
        logger.info("Background tasks successfully cancelled.")

def load_public_shares_metadata():
    """Loads public share metadata from the YAML file into memory."""
    global PUBLIC_SHARES_METADATA
    if not os.path.exists(settings.public_shares_metadata_path):
        PUBLIC_SHARES_METADATA = {}
        return
    try:
        with open(settings.public_shares_metadata_path, "r") as f:
            data = yaml.safe_load(f) or {}
            PUBLIC_SHARES_METADATA = {
                share_id: PublicShareMetadata(**metadata)
                for share_id, metadata in data.items()
            }
        logger.info(f"Loaded {len(PUBLIC_SHARES_METADATA)} public share(s) from metadata file.")
    except (IOError, yaml.YAMLError, ValidationError) as e:
        logger.error(f"Error loading public shares metadata: {e}")
        PUBLIC_SHARES_METADATA = {}

async def save_public_shares_metadata():
    """Saves the in-memory public share metadata to the YAML file."""
    async with METADATA_LOCK:
        try:
            data_to_dump = {
                share_id: metadata.dict(exclude_none=True)
                for share_id, metadata in PUBLIC_SHARES_METADATA.items()
            }
            with tempfile.NamedTemporaryFile('w', delete=False, dir=os.path.dirname(settings.public_shares_metadata_path)) as tf:
                yaml.dump(data_to_dump, tf, sort_keys=False)
                temp_path = tf.name
            shutil.move(temp_path, settings.public_shares_metadata_path)
        except IOError as e:
            logger.error(f"Failed to save public shares metadata: {e}")

async def background_share_cleanup_job():
    """Periodically scans for and deletes expired public shares."""
    while True:
        await asyncio.sleep(settings.share_cleanup_interval_seconds)
        now = time.time()
        expired_ids = [share_id for share_id, meta in PUBLIC_SHARES_METADATA.items() if meta.expiry_timestamp and meta.expiry_timestamp < now]
        if not expired_ids: continue
        logger.info(f"Found {len(expired_ids)} expired share(s) to clean up.")
        for share_id in expired_ids:
            if public_file_path := os.path.join(settings.public_storage_path, share_id):
                if os.path.exists(public_file_path):
                    try: os.remove(public_file_path)
                    except OSError as e: logger.error(f"Error deleting expired share file for {share_id}: {e}")
            del PUBLIC_SHARES_METADATA[share_id]
        await save_public_shares_metadata()
        logger.info("Expired share cleanup complete.")
api_app = FastAPI(title="SealSkin API", lifespan=lifespan)


async def get_decrypted_request_body(request: Request) -> dict:
    session_id = request.headers.get("X-Session-ID")
    if not session_id or session_id not in CRYPTO_SESSIONS:
        raise HTTPException(status_code=400, detail="Invalid or missing session ID")
    aesgcm = AESGCM(CRYPTO_SESSIONS[session_id])
    try:
        encrypted_body = await request.json()
        payload = EncryptedPayload(**encrypted_body)
        decrypted_bytes = aesgcm.decrypt(
            base64.b64decode(payload.iv), base64.b64decode(payload.ciphertext), None
        )
        return json.loads(decrypted_bytes)
    except Exception as e:
        logger.warning(f"Failed to decrypt request for session {session_id[:8]}: {e}")
        raise HTTPException(status_code=400, detail="Failed to decrypt request")


class EncryptedRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            response = await original_handler(request)
            if isinstance(response, JSONResponse) and response.body:
                session_id = request.headers.get("X-Session-ID")
                if session_id in CRYPTO_SESSIONS:
                    aesgcm = AESGCM(CRYPTO_SESSIONS[session_id])
                    iv = os.urandom(12)
                    ciphertext = aesgcm.encrypt(iv, response.body, None)
                    encrypted_payload = EncryptedPayload(
                        iv=base64.b64encode(iv).decode("utf-8"),
                        ciphertext=base64.b64encode(ciphertext).decode("utf-8"),
                    )
                    return JSONResponse(content=encrypted_payload.dict())
            return response

        return custom_handler


async def verify_token(req: Request) -> Dict:
    auth_header = req.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401, detail="Authorization header missing or invalid"
        )
    token = auth_header.split(" ")[1]
    try:
        unverified_claims = jwt.get_unverified_claims(token)
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token format: {e}")
    username = unverified_claims.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Token missing username claim.")
    user = user_manager.get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail=f"User '{username}' not found.")

    effective_settings = user_manager.get_effective_settings(username)
    is_active = user.get("is_admin") or effective_settings.get("active", False)
    if not is_active:
        raise HTTPException(status_code=403, detail="User account is inactive.")
    try:
        public_key = user["public_key"]
        jwt.decode(token, public_key, algorithms=[ALGORITHM])
        user["effective_settings"] = effective_settings
        user["group"] = effective_settings.get("group", "none")
        return user
    except KeyError:
        raise HTTPException(
            status_code=500, detail="Server configuration error for user."
        )
    except JWTError as e:
        raise HTTPException(
            status_code=401, detail=f"Invalid token signature or claims: {e}"
        )


async def verify_admin(user: dict = Depends(verify_token)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin privileges required.")
    return user


async def verify_persistent_storage_enabled(user: dict = Depends(verify_token)) -> dict:
    if not user.get("effective_settings", {}).get("persistent_storage", False):
        raise HTTPException(
            status_code=403, detail="Persistent storage is disabled for this account."
        )
    return user

async def verify_public_sharing_enabled(user: dict = Depends(verify_persistent_storage_enabled)) -> dict:
    """Verifies that the user is an admin or has public sharing enabled."""
    if user.get("is_admin"):
        return user
    if user.get("effective_settings", {}).get("public_sharing", False):
        return user
    raise HTTPException(
        status_code=403, detail="Public file sharing is disabled for this account."
    )

@api_app.post("/api/handshake/initiate", response_model=HandshakeInitiateResponse)
async def handshake_initiate():
    nonce = os.urandom(32)
    signature = SERVER_PRIVATE_KEY.sign(
        nonce,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    return {
        "nonce": base64.b64encode(nonce).decode("utf-8"),
        "signature": base64.b64encode(signature).decode("utf-8"),
    }


@api_app.post("/api/handshake/exchange", response_model=HandshakeExchangeResponse)
async def handshake_exchange(request: HandshakeExchangeRequest):
    try:
        aes_key = SERVER_PRIVATE_KEY.decrypt(
            base64.b64decode(request.encrypted_session_key),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        session_id = str(uuid.uuid4())
        CRYPTO_SESSIONS[session_id] = aes_key
        logger.info(
            f"E2EE handshake successful. New crypto session: {session_id[:8]}..."
        )
        return {"session_id": session_id}
    except Exception as e:
        logger.error(f"Failed to decrypt session key during handshake: {e}")
        raise HTTPException(status_code=400, detail="Failed to decrypt session key")


encrypted_router = APIRouter(route_class=EncryptedRoute)


async def _reassemble_file(upload_id: str, total_chunks: int, filename: str) -> str:
    """Reassembles chunks into a single file and returns its path."""
    upload_dir = os.path.join(settings.upload_dir, upload_id)
    if not os.path.isdir(upload_dir):
        raise HTTPException(status_code=404, detail="Upload session not found.")

    for i in range(total_chunks):
        if not os.path.exists(os.path.join(upload_dir, f"chunk_{i}")):
            raise HTTPException(
                status_code=400, detail=f"Missing chunk {i} for upload."
            )

    fd, temp_path = tempfile.mkstemp(dir=settings.upload_dir, prefix=f"{upload_id}-")

    try:
        with os.fdopen(fd, "wb") as final_file:
            for i in range(total_chunks):
                chunk_path = os.path.join(upload_dir, f"chunk_{i}")
                with open(chunk_path, "rb") as chunk_file:
                    final_file.write(chunk_file.read())

        await asyncio.to_thread(shutil.rmtree, upload_dir, ignore_errors=True)

        return temp_path
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        await asyncio.to_thread(shutil.rmtree, upload_dir, ignore_errors=True)
        logger.error(f"Failed to reassemble file for upload {upload_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to reassemble file.")

def _get_unique_filename(directory: str, filename: str) -> str:
    """
    Checks if a filename exists in a directory. If so, appends '-1', '-2', etc.
    until a unique name is found. Returns the unique, non-conflicting filename.
    """
    if not os.path.exists(os.path.join(directory, filename)):
        return filename

    name, ext = os.path.splitext(filename)
    counter = 1
    while True:
        new_filename = f"{name}-{counter}{ext}"
        if not os.path.exists(os.path.join(directory, new_filename)):
            return new_filename
        counter += 1


@encrypted_router.post("/api/applications", response_model=List[Application])
async def get_applications(user: dict = Depends(verify_token)):
    user_apps = []
    username = user["username"]
    user_group = user.get("group", "none")

    for app in INSTALLED_APPS.values():
        allowed_users = app.users
        allowed_groups = app.groups
        if (
            ("all" in allowed_users)
            or (username in allowed_users)
            or ("all" in allowed_groups)
            or (user_group in allowed_groups)
        ):
            user_apps.append(
                Application(
                    id=app.id,
                    name=app.name,
                    logo=app.logo,
                    home_directories=app.home_directories,
                    nvidia_support=app.provider_config.nvidia_support,
                    dri3_support=app.provider_config.dri3_support,
                    url_support=app.provider_config.url_support,
                    extensions=app.provider_config.extensions,
                )
            )

    return sorted(user_apps, key=lambda x: x.name.lower())


async def _stop_session(session_id: str):
    """Helper function to stop a session and clean up its resources."""
    logger.info(f"[{session_id}] Stopping session...")
    if session_data := SESSIONS_DB.pop(session_id, None):
        app_id = session_data.get("provider_app_id")
        app_config = INSTALLED_APPS.get(app_id)
        if app_config:
            provider = DockerProvider(app_config.dict())
            await provider.stop(session_data["instance_id"])

        host_mount_path = session_data.get("host_mount_path")
        await save_sessions_to_disk()
        ephemeral_base_path = os.path.join(settings.storage_path, "sealskin_ephemeral")
        if host_mount_path and host_mount_path.startswith(ephemeral_base_path):
            if os.path.exists(host_mount_path):
                await asyncio.to_thread(
                    shutil.rmtree, host_mount_path, ignore_errors=True
                )
                logger.info(f"[{session_id}] Removed ephemeral storage directory.")
        logger.info(f"[{session_id}] Session stopped and cleaned up successfully.")
    else:
        logger.warning(
            f"Attempted to stop session {session_id}, but it was not found in the database."
        )


async def _launch_common(
    application_id: str,
    username: str,
    effective_settings: dict,
    home_name: Optional[str],
    env_vars: dict,
    language: Optional[str],
    selected_gpu: Optional[str],
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    open_file_on_launch: bool = True,
) -> dict:
    app_config = INSTALLED_APPS.get(application_id)
    if not app_config:
        raise HTTPException(
            status_code=404, detail=f"Application with ID '{application_id}' not found."
        )

    session_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)
    subfolder = f"/{session_id}/"
    launch_context = None

    custom_user = str(uuid.uuid4())
    password = str(uuid.uuid4())

    final_env = {
        "SUBFOLDER": subfolder,
        "PUID": str(settings.puid),
        "PGID": str(settings.pgid),
        "CUSTOM_USER": custom_user,
        "PASSWORD": password,
    }

    template_name = app_config.app_template
    template = APP_TEMPLATES.get(template_name)
    if template and template.get("settings"):
        template_settings = {k: str(v) for k, v in template["settings"].items()}
        final_env.update(template_settings)
    elif not template:
        logger.warning(
            f"[{session_id}] Template '{template_name}' not found for app '{app_config.name}'. Using container defaults."
        )

    final_env.update(env_vars)
    if "SEALSKIN_URL" in final_env:
        launch_context = {"type": "url", "value": final_env["SEALSKIN_URL"]}
    if language and language.lower() != "en_us.utf-8":
        final_env["LC_ALL"] = language
    if app_config.provider_config.env:
        for env_override in app_config.provider_config.env:
            final_env[env_override.name] = env_override.value

    provider = DockerProvider(app_config.dict())
    volumes = {}
    host_mount_path = None

    gpu_config = None
    if selected_gpu and effective_settings.get("gpu", False):
        gpu_info = next(
            (gpu for gpu in AVAILABLE_GPUS if gpu["device"] == selected_gpu), None
        )
        if not gpu_info:
            raise HTTPException(
                status_code=400,
                detail=f"Selected GPU '{selected_gpu}' is not available.",
            )
        if (
            gpu_info["type"] == "nvidia"
            and not app_config.provider_config.nvidia_support
        ):
            raise HTTPException(
                status_code=400,
                detail=f"App '{app_config.name}' does not support Nvidia GPUs.",
            )
        if gpu_info["type"] == "dri3" and not app_config.provider_config.dri3_support:
            raise HTTPException(
                status_code=400,
                detail=f"App '{app_config.name}' does not support DRI3 GPUs.",
            )
        gpu_config = gpu_info
        if gpu_config["type"] == "dri3":
            final_env["DRI_NODE"] = gpu_config["device"]
            final_env["DRINODE"] = gpu_config["device"]

    use_persistent_storage = (
        effective_settings.get("persistent_storage", False)
        and app_config.home_directories
    )
    if not use_persistent_storage:
        home_name = "cleanroom"

    if home_name and home_name.lower() != "cleanroom":
        host_mount_path = os.path.abspath(
            os.path.join(settings.storage_path, username, home_name)
        )
        if not os.path.isdir(host_mount_path):
            raise HTTPException(
                status_code=404, detail=f"Home directory '{home_name}' not found."
            )
        shared_files_path = os.path.abspath(
            os.path.join(settings.storage_path, username, "_sealskin_shared_files")
        )
        os.makedirs(shared_files_path, exist_ok=True, mode=0o755)
    elif file_bytes and filename:
        host_mount_path = os.path.join(settings.storage_path, "sealskin_ephemeral", str(uuid.uuid4()))

    autostart_content = None
    if app_config.provider_config.custom_autostart_script_b64:
        try:
            autostart_content = base64.b64decode(
                app_config.provider_config.custom_autostart_script_b64
            ).decode("utf-8")
            logger.info(f"[{session_id}] Using custom autostart script for '{app_config.name}'.")
        except Exception as e:
            logger.error(f"[{session_id}] Failed to decode custom autostart script: {e}")
    elif app_config.provider_config.autostart:
        autostart_cache_path = _get_autostart_cache_path(app_config)
        if (
            autostart_cache_path
            and os.path.exists(autostart_cache_path)
            and os.path.getsize(autostart_cache_path) > 0
        ):
            try:
                with open(autostart_cache_path, "r") as f:
                    autostart_content = f.read()
                logger.info(f"[{session_id}] Using cached repository autostart script for '{app_config.name}'.")
            except Exception as e:
                logger.error(f"[{session_id}] Failed to read cached autostart script: {e}")

    if autostart_content:
        if not host_mount_path:
            host_mount_path = os.path.join(settings.storage_path, "sealskin_ephemeral", str(uuid.uuid4()))
            logger.info(f"[{session_id}] Created ephemeral storage for autostart script.")
        
        autostart_dir = os.path.join(host_mount_path, ".config", "openbox")
        autostart_path = os.path.join(autostart_dir, "autostart")
        try:
            os.makedirs(autostart_dir, exist_ok=True, mode=0o755)
            with open(autostart_path, "w") as f:
                f.write(autostart_content)
            os.chmod(autostart_path, 0o755)
            logger.info(f"[{session_id}] Successfully wrote autostart script to session storage.")
        except Exception as e:
            logger.error(f"[{session_id}] Failed to write autostart script: {e}")

    if host_mount_path:
        translated_host_mount_path = _translate_path_to_host(host_mount_path)
        volumes[translated_host_mount_path] = {
            "bind": settings.container_config_path,
            "mode": "rw",
        }
        if home_name and home_name.lower() != "cleanroom":
            translated_shared_files_path = _translate_path_to_host(shared_files_path)
            volumes[translated_shared_files_path] = {
                "bind": os.path.join(settings.container_config_path, "Desktop", "files"),
                "mode": "rw",
            }

        if file_bytes and filename:
            if home_name and home_name.lower() != "cleanroom":
                file_dest_dir = shared_files_path
            else:
                file_dest_dir = os.path.join(host_mount_path, "Desktop", "files")

            os.makedirs(file_dest_dir, exist_ok=True, mode=0o755)
            actual_filename = _get_unique_filename(file_dest_dir, filename)
            file_location = os.path.join(file_dest_dir, actual_filename)

            with open(file_location, "wb") as f:
                f.write(file_bytes)
            os.chmod(file_location, 0o644)
            if open_file_on_launch:
                container_file_path = os.path.join(
                    settings.container_config_path, "Desktop", "files", actual_filename
                )
                final_env["SEALSKIN_FILE"] = container_file_path
                launch_context = {"type": "file", "value": filename}

    try:
        instance_details = await provider.launch(
            session_id, final_env, volumes, gpu_config
        )
        SESSIONS_DB[session_id] = {
            "instance_id": instance_details["instance_id"],
            "ip": instance_details["ip"],
            "port": instance_details["port"],
            "created_at": time.time(),
            "access_token": access_token,
            "provider_app_id": application_id,
            "username": username,
            "app_name": app_config.name,
            "app_logo": app_config.logo,
            "host_mount_path": host_mount_path,
            "launch_context": launch_context,
            "custom_user": custom_user,
            "password": password,
        }
        logger.info(
            f"[{session_id}] Session ready for {username}. Proxying to {instance_details['ip']}:{instance_details['port']}"
        )
        return {"session_url": f"/{session_id}/?access_token={access_token}"}
    except Exception as e:
        if host_mount_path and host_mount_path.startswith(os.path.join(settings.storage_path, "sealskin_ephemeral")):
            shutil.rmtree(host_mount_path, ignore_errors=True)
        logger.error(
            f"[{session_id}] Unhandled exception during launch for app '{application_id}': {e}",
            exc_info=True,
        )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred during application launch.",
        )


@encrypted_router.post("/api/launch/simple", response_model=LaunchResponse)
async def launch_simple(
    decrypted_body: dict = Depends(get_decrypted_request_body),
    auth_user: dict = Depends(verify_token),
):
    try:
        req = LaunchRequestSimple(**decrypted_body)
        return await _launch_common(
            req.application_id,
            auth_user["username"],
            auth_user["effective_settings"],
            req.home_name,
            {},
            req.language,
            req.selected_gpu,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@encrypted_router.post("/api/launch/url", response_model=LaunchResponse)
async def launch_url(
    decrypted_body: dict = Depends(get_decrypted_request_body),
    auth_user: dict = Depends(verify_token),
):
    try:
        req = LaunchRequestURL(**decrypted_body)
        return await _launch_common(
            req.application_id,
            auth_user["username"],
            auth_user["effective_settings"],
            req.home_name,
            {"SEALSKIN_URL": req.url},
            req.language,
            req.selected_gpu,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@encrypted_router.post("/api/launch/file", response_model=LaunchResponse)
async def launch_file(
    decrypted_body: dict = Depends(get_decrypted_request_body),
    auth_user: dict = Depends(verify_token),
):
    try:
        req = LaunchRequestFile(**decrypted_body)
        reassembled_file_path = await _reassemble_file(
            req.upload_id, req.total_chunks, req.filename
        )

        file_bytes = None
        try:
            with open(reassembled_file_path, "rb") as f:
                file_bytes = f.read()
        finally:
            if os.path.exists(reassembled_file_path):
                os.remove(reassembled_file_path)

        return await _launch_common(
            req.application_id,
            auth_user["username"],
            auth_user["effective_settings"],
            req.home_name,
            {},
            req.language,
            req.selected_gpu,
            file_bytes,
            os.path.basename(req.filename),
            req.open_file_on_launch,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


session_router = APIRouter(
    prefix="/api/sessions",
    dependencies=[Depends(verify_token)],
    route_class=EncryptedRoute,
)


@session_router.get("", response_model=List[ActiveSessionInfo])
async def get_my_sessions(user: dict = Depends(verify_token)):
    user_sessions = []
    for sid, s_data in SESSIONS_DB.items():
        if s_data.get("username") == user["username"]:
            user_sessions.append(
                ActiveSessionInfo(
                    session_id=sid,
                    app_id=s_data["provider_app_id"],
                    app_name=s_data["app_name"],
                    app_logo=s_data["app_logo"],
                    created_at=s_data["created_at"],
                    session_url=f"/{sid}/?access_token={s_data['access_token']}",
                    launch_context=s_data.get("launch_context"),
                )
            )
    return sorted(user_sessions, key=lambda s: s.created_at, reverse=True)


@session_router.delete("/{session_id}", status_code=204)
async def stop_my_session(session_id: str, user: dict = Depends(verify_token)):
    session_data = SESSIONS_DB.get(session_id)
    if not session_data or session_data.get("username") != user["username"]:
        raise HTTPException(
            status_code=404, detail="Session not found or permission denied."
        )
    await _stop_session(session_id)
    return Response(status_code=204)


@session_router.post("/{session_id}/send_file")
async def send_file_to_session(
    session_id: str,
    decrypted_body: dict = Depends(get_decrypted_request_body),
    user: dict = Depends(verify_token),
):
    try:
        req = SendFileToSessionRequest(**decrypted_body)
        session_data = SESSIONS_DB.get(session_id)
        if not session_data or session_data.get("username") != user["username"]:
            raise HTTPException(
                status_code=404, detail="Session not found or permission denied."
            )

        host_mount_path = session_data.get("host_mount_path")
        if not host_mount_path:
            raise HTTPException(
                status_code=400,
                detail="Cannot send files to this session as it has no mounted storage.",
            )

        reassembled_file_path = await _reassemble_file(
            req.upload_id, req.total_chunks, req.filename
        )
        safe_filename = os.path.basename(req.filename)

        is_persistent = host_mount_path and not host_mount_path.startswith(
            os.path.join(settings.storage_path, "sealskin_ephemeral")
        )

        if is_persistent:
            file_dest_dir = os.path.abspath(
                os.path.join(settings.storage_path, user["username"], "_sealskin_shared_files")
            )
        else:
            file_dest_dir = os.path.join(host_mount_path, "Desktop", "files")

        os.makedirs(file_dest_dir, exist_ok=True, mode=0o755)

        actual_filename = await asyncio.to_thread(_get_unique_filename, file_dest_dir, safe_filename)
        file_location = os.path.join(file_dest_dir, actual_filename)

        try:
            await asyncio.to_thread(shutil.move, reassembled_file_path, file_location)
            await asyncio.to_thread(os.chmod, file_location, 0o644)
        except Exception as e:
            if os.path.exists(reassembled_file_path):
                os.remove(reassembled_file_path)
            logger.error(f"Failed to move reassembled file to session storage: {e}")
            raise HTTPException(
                status_code=500, detail="Could not place file in session storage."
            )

        logger.info(
            f"[{session_id}] User '{user['username']}' wrote file '{actual_filename}' (as '{safe_filename}') to session."
        )
        return {
            "status": "success",
            "message": f"File '{safe_filename}' sent to session.",
        }
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


homedir_router = APIRouter(
    prefix="/api/homedirs",
    dependencies=[Depends(verify_persistent_storage_enabled)],
    route_class=EncryptedRoute,
)


@homedir_router.get("", response_model=HomeDirectoryList)
async def list_my_home_dirs(user: dict = Depends(verify_persistent_storage_enabled)):
    return {"home_dirs": user_manager.get_home_dirs(user["username"])}


@homedir_router.post("", status_code=201)
async def create_my_home_dir(
    decrypted_body: dict = Depends(get_decrypted_request_body),
    user: dict = Depends(verify_persistent_storage_enabled),
):
    try:
        req = HomeDirectoryCreate(**decrypted_body)
        user_manager.create_home_dir(user["username"], req.home_name)
        return {"status": "success", "home_name": req.home_name}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@homedir_router.delete("/{home_name}", status_code=204)
async def delete_my_home_dir(
    home_name: str, user: dict = Depends(verify_persistent_storage_enabled)
):
    try:
        user_manager.delete_home_dir(user["username"], home_name)
        return Response(status_code=204)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


admin_router = APIRouter(
    prefix="/api/admin",
    dependencies=[Depends(verify_admin)],
    route_class=EncryptedRoute,
)


@encrypted_router.post("/api/admin/status", response_model=AdminStatusResponse)
async def admin_status(user: dict = Depends(verify_token)):
    stats = _get_system_stats()
    response = {
        "is_admin": user.get("is_admin", False),
        "username": user.get("username"),
        "settings": user.get("effective_settings"),
        "gpus": [],
        **stats,
    }
    if user.get("effective_settings", {}).get("gpu", False):
        response["gpus"] = [
            GPUInfo(device=gpu["device"], driver=gpu["driver"])
            for gpu in AVAILABLE_GPUS
        ]
    return response


@admin_router.post("/data", response_model=ManagementDataResponse)
async def get_management_data():
    return {
        "admins": user_manager.get_all_admins(),
        "users": user_manager.get_all_users(),
        "groups": user_manager.get_all_groups(),
        "server_public_key": SERVER_PUBLIC_KEY_PEM,
        "api_port": DISCOVERED_API_PORT,
        "session_port": DISCOVERED_SESSION_PORT,
        "gpus": [
            GPUInfo(device=gpu["device"], driver=gpu["driver"])
            for gpu in AVAILABLE_GPUS
        ],
    }


@admin_router.get("/apps/stores", response_model=List[AppStore])
async def get_app_stores():
    return APP_STORES


@admin_router.post("/apps/stores", response_model=AppStore, status_code=201)
async def add_app_store(decrypted_body: dict = Depends(get_decrypted_request_body)):
    store = AppStore(**decrypted_body)
    if any(s.name == store.name for s in APP_STORES):
        raise HTTPException(
            status_code=409,
            detail=f"App store with name '{store.name}' already exists.",
        )
    APP_STORES.append(store)
    save_app_stores()
    return store


@admin_router.delete("/apps/stores/{store_name}", status_code=204)
async def delete_app_store(store_name: str):
    store_found = next((s for s in APP_STORES if s.name == store_name), None)
    if not store_found:
        raise HTTPException(status_code=404, detail="App store not found.")
    APP_STORES.remove(store_found)
    save_app_stores()
    return Response(status_code=204)

@admin_router.get("/apps/available", response_model=List[AvailableApp])
async def get_available_apps(url: str, store_name: str): # <-- ADD store_name parameter
    async def fetch_and_process_store(content: str, store_name_for_cache: str):
        """Loads, flattens, and returns the list of apps from YAML content."""
        try:

            def extract_apps_from_data(data):
                if isinstance(data, dict) and "apps" in data:
                    return data["apps"]
                elif isinstance(data, list):
                    return data
                else:
                    raise HTTPException(
                        status_code=500, detail="App store YAML has an invalid format."
                    )

            loaded_data = yaml.safe_load(content)
            apps_list = extract_apps_from_data(loaded_data)
            for app in apps_list:
                provider_config = app.get("provider_config", {})
                
                if provider_config.get("autostart"):
                    app_id = app.get("id")
                    if app_id:
                        cache_dir = os.path.join(settings.autostart_cache_path, store_name_for_cache)
                        cache_file_path = os.path.join(cache_dir, app_id)
                        script_content_b64 = None
                        if os.path.exists(cache_file_path) and os.path.getsize(cache_file_path) > 0:
                            try:
                                with open(cache_file_path, "rb") as f:
                                    script_content = f.read()
                                script_content_b64 = base64.b64encode(script_content).decode("utf-8")
                            except Exception as e:
                                logger.error(f"Failed to read/encode autostart cache for {app_id}: {e}")
                        provider_config["custom_autostart_script_b64"] = script_content_b64

                if (
                    "extensions" in provider_config
                    and provider_config["extensions"]
                ):
                    original_extensions = provider_config["extensions"]
                    flattened_extensions = []
                    for item in original_extensions:
                        if isinstance(item, list):
                            flattened_extensions.extend(item)
                        else:
                            flattened_extensions.append(item)
                    provider_config["extensions"] = flattened_extensions

            return apps_list

        except (yaml.YAMLError, ValidationError) as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to parse app store YAML: {e}"
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"An error occurred while processing the app store: {e}",
            )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, follow_redirects=True, timeout=15)
            response.raise_for_status()
            return await fetch_and_process_store(response.text, store_name)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=400, detail=f"Failed to fetch app store from URL '{url}': {e}"
        )

@admin_router.get("/apps/installed", response_model=List[InstalledAppWithStatus])
async def list_installed_apps():
    response_apps = []
    for app in INSTALLED_APPS.values():
        app_dict = app.dict()
        image_name = app.provider_config.image
        metadata = IMAGE_METADATA.get(image_name, {})
        app_dict["image_sha"] = metadata.get("sha")
        app_dict["last_checked_at"] = metadata.get("last_checked_at")
        app_dict["pull_status"] = PULL_STATUS.get(image_name)
        response_apps.append(InstalledAppWithStatus(**app_dict))
    return sorted(response_apps, key=lambda x: x.name.lower())


@admin_router.post("/apps/installed", response_model=InstalledApp, status_code=201)
async def install_app(decrypted_body: dict = Depends(get_decrypted_request_body)):
    app = InstalledApp(**decrypted_body)
    if app.id in INSTALLED_APPS:
        raise HTTPException(status_code=409, detail="App with this ID already exists.")
    INSTALLED_APPS[app.id] = app
    save_installed_apps()
    asyncio.create_task(_pull_and_cache_image(app.provider_config.image))
    return app


@admin_router.put("/apps/installed/{app_id}", response_model=InstalledApp)
async def update_installed_app(
    app_id: str, decrypted_body: dict = Depends(get_decrypted_request_body)
):
    app_update = InstalledApp(**decrypted_body)
    if app_id not in INSTALLED_APPS:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    if app_id != app_update.id:
        raise HTTPException(
            status_code=400, detail="App ID in path does not match body."
        )

    if app_update.provider_config.custom_autostart_script_b64 == "":
        app_update.provider_config.custom_autostart_script_b64 = None

    old_image_name = INSTALLED_APPS[app_id].provider_config.image

    INSTALLED_APPS[app_id] = app_update
    save_installed_apps()

    if old_image_name != app_update.provider_config.image:
        asyncio.create_task(_pull_and_cache_image(app_update.provider_config.image))

    return app_update


@admin_router.delete("/apps/installed/{app_id}", status_code=204)
async def delete_installed_app(app_id: str):
    if app_id not in INSTALLED_APPS:
        raise HTTPException(status_code=404, detail="Installed app not found.")

    app_to_delete = INSTALLED_APPS[app_id]

    del INSTALLED_APPS[app_id]
    save_installed_apps()
    return Response(status_code=204)


@admin_router.post(
    "/apps/installed/{app_id}/check_update", response_model=ImageUpdateCheckResponse
)
async def check_app_update(app_id: str):
    if app_id not in INSTALLED_APPS:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    app = INSTALLED_APPS[app_id]
    image_name = app.provider_config.image

    provider = DockerProvider(app.dict())
    local_info = await provider.get_local_image_info(image_name)
    remote_digest = await provider.get_remote_image_digest(image_name)

    if not remote_digest:
        raise HTTPException(
            status_code=502,
            detail=f"Could not retrieve update information for {image_name} from its registry.",
        )

    local_digests = local_info.get("digests", []) if local_info else []
    update_available = not any(
        remote_digest in local_digest for local_digest in local_digests
    )

    return ImageUpdateCheckResponse(
        current_sha=local_info["short_id"] if local_info else None,
        update_available=update_available,
    )


@admin_router.post(
    "/apps/installed/{app_id}/pull_latest", response_model=ImagePullResponse
)
async def pull_latest_app_image(app_id: str):
    if app_id not in INSTALLED_APPS:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    app = INSTALLED_APPS[app_id]
    image_name = app.provider_config.image

    try:
        provider = DockerProvider(app.dict())
        await provider.pull_image(image_name)
        await _update_autostart_cache(app)
        await _get_and_cache_image_metadata(image_name, force_refresh=True)
        new_sha = IMAGE_METADATA.get(image_name, {}).get("sha")
        return ImagePullResponse(status="success", new_sha=new_sha)
    except Exception as e:
        logger.error(f"Error pulling image for app {app_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to pull image: {str(e)}")


@admin_router.get("/apps/templates", response_model=List[AppTemplate])
async def get_app_templates():
    return sorted(list(APP_TEMPLATES.values()), key=lambda x: x["name"])


@admin_router.post("/apps/templates", response_model=AppTemplate, status_code=201)
async def save_app_template(decrypted_body: dict = Depends(get_decrypted_request_body)):
    try:
        template = AppTemplate(**decrypted_body)
        if not re.match(r"^[a-zA-Z0-9_ -]+$", template.name):
            raise HTTPException(status_code=400, detail="Invalid template name.")

        filename = template.name.lower().replace(" ", "_") + ".yml"
        file_path = os.path.join(settings.app_templates_path, filename)

        with open(file_path, "w") as f:
            yaml.dump(template.dict(), f, sort_keys=False)

        load_app_templates()
        return template
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Error saving app template: {e}")
        raise HTTPException(
            status_code=500, detail="Internal server error while saving template."
        )


@admin_router.delete("/apps/templates/{template_name}", status_code=204)
async def delete_app_template(template_name: str):
    filename = template_name.lower().replace(" ", "_") + ".yml"

    user_path = os.path.join(settings.app_templates_path, filename)
    default_path = os.path.join(settings.default_app_templates_path, filename)

    if os.path.exists(user_path):
        try:
            os.remove(user_path)
            load_app_templates()
            return Response(status_code=204)
        except OSError as e:
            logger.error(f"Error deleting template file '{user_path}': {e}")
            raise HTTPException(
                status_code=500, detail="Failed to delete template file."
            )
    elif os.path.exists(default_path):
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete the default template '{template_name}'. You can override it by creating a new template with the same name.",
        )
    else:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_name}' not found."
        )


@admin_router.get("/sessions", response_model=List[UserSessionList])
async def get_all_sessions():
    sessions_by_user = defaultdict(list)
    for sid, s_data in SESSIONS_DB.items():
        username = s_data.get("username", "unknown")
        sessions_by_user[username].append(
            ActiveSessionInfo(
                session_id=sid,
                app_id=s_data["provider_app_id"],
                app_name=s_data["app_name"],
                app_logo=s_data["app_logo"],
                created_at=s_data["created_at"],
                session_url=f"/{sid}/?access_token={s_data['access_token']}",
                launch_context=s_data.get("launch_context"),
            )
        )

    response = [
        UserSessionList(
            username=uname,
            sessions=sorted(slist, key=lambda s: s.created_at, reverse=True),
        )
        for uname, slist in sessions_by_user.items()
    ]
    return sorted(response, key=lambda u: u.username)


@admin_router.delete("/sessions/{session_id}", status_code=204)
async def stop_any_session(session_id: str):
    if session_id not in SESSIONS_DB:
        raise HTTPException(status_code=404, detail="Session not found.")
    await _stop_session(session_id)
    return Response(status_code=204)


@admin_router.post("/admins", response_model=CreateUserResponse, status_code=201)
async def create_admin(decrypted_body: dict = Depends(get_decrypted_request_body)):
    try:
        req = CreateAdminRequest(**decrypted_body)
        user, pk = user_manager.create_admin(req.username, req.public_key)
        user_manager.load_users_and_groups()
        return {"user": user, "private_key": pk}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@admin_router.delete("/admins/{username}", status_code=204)
async def delete_admin(username: str):
    try:
        user_manager.delete_admin(username)
        user_manager.load_users_and_groups()
        return Response(status_code=204)
    except ValueError as e:
        if "cannot be deleted" in str(e).lower():
            raise HTTPException(status_code=403, detail=str(e))
        raise HTTPException(status_code=404, detail=str(e))


@admin_router.post("/users", response_model=CreateUserResponse, status_code=201)
async def create_user(decrypted_body: dict = Depends(get_decrypted_request_body)):
    try:
        req = CreateUserRequest(**decrypted_body)
        user, pk = user_manager.create_user(
            req.username, req.public_key, req.settings.dict()
        )
        user_manager.load_users_and_groups()
        return {"user": user, "private_key": pk}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@admin_router.put("/users/{username}", response_model=User)
async def update_user(
    username: str, decrypted_body: dict = Depends(get_decrypted_request_body)
):
    try:
        req = UpdateUserRequest(**decrypted_body)
        user_manager.update_user_settings(username, req.settings.dict())
        user_manager.load_users_and_groups()
        return user_manager.get_user(username)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@admin_router.delete("/users/{username}", status_code=204)
async def delete_user(username: str):
    try:
        user_manager.delete_user(username)
        user_manager.load_users_and_groups()
        return Response(status_code=204)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@admin_router.get("/users/{username}/homedirs", response_model=HomeDirectoryList)
async def list_user_home_dirs(username: str):
    if not user_manager.get_user(username):
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    if not user_manager.get_effective_settings(username).get(
        "persistent_storage", False
    ):
        raise HTTPException(
            status_code=403, detail="Persistent storage is disabled for this user."
        )
    return {"home_dirs": user_manager.get_home_dirs(username)}


@admin_router.post("/users/{username}/homedirs", status_code=201)
async def create_user_home_dir(
    username: str, decrypted_body: dict = Depends(get_decrypted_request_body)
):
    if not user_manager.get_user(username):
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    if not user_manager.get_effective_settings(username).get(
        "persistent_storage", False
    ):
        raise HTTPException(
            status_code=403, detail="Persistent storage is disabled for this user."
        )
    try:
        req = HomeDirectoryCreate(**decrypted_body)
        user_manager.create_home_dir(username, req.home_name)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@admin_router.delete("/users/{username}/homedirs/{home_name}", status_code=204)
async def delete_user_home_dir(username: str, home_name: str):
    if not user_manager.get_user(username):
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    if not user_manager.get_effective_settings(username).get(
        "persistent_storage", False
    ):
        raise HTTPException(
            status_code=403, detail="Persistent storage is disabled for this user."
        )
    try:
        user_manager.delete_home_dir(username, home_name)
        return Response(status_code=204)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@admin_router.get("/admins/{username}/homedirs", response_model=HomeDirectoryList)
async def list_admin_home_dirs(username: str):
    user = user_manager.get_user(username)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=404, detail=f"Admin '{username}' not found.")
    return {"home_dirs": user_manager.get_home_dirs(username)}


@admin_router.post("/admins/{username}/homedirs", status_code=201)
async def create_admin_home_dir(
    username: str, decrypted_body: dict = Depends(get_decrypted_request_body)
):
    user = user_manager.get_user(username)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=404, detail=f"Admin '{username}' not found.")
    try:
        req = HomeDirectoryCreate(**decrypted_body)
        user_manager.create_home_dir(username, req.home_name)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@admin_router.delete("/admins/{username}/homedirs/{home_name}", status_code=204)
async def delete_admin_home_dir(username: str, home_name: str):
    user = user_manager.get_user(username)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=404, detail=f"Admin '{username}' not found.")
    try:
        user_manager.delete_home_dir(username, home_name)
        return Response(status_code=204)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@admin_router.post("/groups", response_model=Group, status_code=201)
async def create_group(decrypted_body: dict = Depends(get_decrypted_request_body)):
    try:
        req = CreateGroupRequest(**decrypted_body)
        if req.name in user_manager.GROUP_DATA:
            raise HTTPException(
                status_code=409, detail=f"Group '{req.name}' already exists."
            )
        user_manager.write_group_file(req.name, req.settings.dict())
        user_manager.load_users_and_groups()
        return user_manager.GROUP_DATA.get(req.name)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@admin_router.put("/groups/{group_name}", response_model=Group)
async def update_group(
    group_name: str, decrypted_body: dict = Depends(get_decrypted_request_body)
):
    try:
        req = UpdateGroupRequest(**decrypted_body)
        if group_name not in user_manager.GROUP_DATA:
            raise HTTPException(
                status_code=404, detail=f"Group '{group_name}' not found."
            )
        user_manager.write_group_file(group_name, req.settings.dict())
        user_manager.load_users_and_groups()
        return user_manager.GROUP_DATA.get(group_name)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@admin_router.delete("/groups/{group_name}", status_code=204)
async def delete_group(group_name: str):
    try:
        user_manager.delete_group(group_name)
        user_manager.load_users_and_groups()
        return Response(status_code=204)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


upload_router = APIRouter(
    prefix="/api/upload",
    dependencies=[Depends(verify_token)],
    route_class=EncryptedRoute,
)


@upload_router.post("/initiate", response_model=UploadInitiateResponse)
async def upload_initiate(decrypted_body: dict = Depends(get_decrypted_request_body)):
    try:
        req = UploadInitiateRequest(**decrypted_body)
        upload_id = str(uuid.uuid4())
        upload_path = os.path.join(settings.upload_dir, upload_id)
        os.makedirs(upload_path, exist_ok=True, mode=0o700)

        with open(os.path.join(upload_path, "metadata.json"), "w") as f:
            json.dump(
                {
                    "filename": req.filename,
                    "size": req.total_size,
                    "started": time.time(),
                },
                f,
            )

        return {"upload_id": upload_id}
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


@upload_router.post("/chunk")
async def upload_chunk(decrypted_body: dict = Depends(get_decrypted_request_body)):
    try:
        req = UploadChunkRequest(**decrypted_body)
        upload_path = os.path.join(settings.upload_dir, req.upload_id)
        if not os.path.isdir(upload_path):
            raise HTTPException(status_code=404, detail="Upload session not found.")

        chunk_path = os.path.join(upload_path, f"chunk_{req.chunk_index}")
        with open(chunk_path, "wb") as f:
            f.write(base64.b64decode(req.chunk_data_b64))

        return {"status": "ok", "chunk_index": req.chunk_index}
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid Base64 chunk data: {e}")


@upload_router.post(
    "/to_storage", dependencies=[Depends(verify_persistent_storage_enabled)]
)
async def upload_to_storage(
    decrypted_body: dict = Depends(get_decrypted_request_body),
    user: dict = Depends(verify_persistent_storage_enabled),
):
    try:
        req = UploadToStorageRequest(**decrypted_body)
        username = user["username"]

        available_homes = user_manager.get_home_dirs(username)
        if req.home_name not in available_homes:
            raise HTTPException(
                status_code=404,
                detail=f"Home directory '{req.home_name}' not found for user.",
            )

        reassembled_file_path = await _reassemble_file(
            req.upload_id, req.total_chunks, req.filename
        )

        safe_filename = os.path.basename(req.filename)
        file_dest_dir = os.path.join(
            settings.storage_path, username, "_sealskin_shared_files"
        )

        await asyncio.to_thread(os.makedirs, file_dest_dir, exist_ok=True, mode=0o755)

        actual_filename = await asyncio.to_thread(
            _get_unique_filename, file_dest_dir, safe_filename
        )
        file_location = os.path.join(file_dest_dir, actual_filename)

        try:
            await asyncio.to_thread(shutil.move, reassembled_file_path, file_location)
            await asyncio.to_thread(os.chmod, file_location, 0o644)
        except Exception as e:
            if os.path.exists(reassembled_file_path):
                os.remove(reassembled_file_path)
            logger.error(
                f"Failed to move reassembled file to session storage for user '{username}': {e}"
            )
            raise HTTPException(
                status_code=500, detail="Could not place file in session storage."
            )

        logger.info(
            f"User '{username}' uploaded file '{actual_filename}' (as '{safe_filename}') to shared storage."
        )
        return {
            "status": "success",
            "message": f"File '{safe_filename}' uploaded successfully.",
        }

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")

def _get_validated_path(username: str, home_dir: str, sub_path: str, check_existence: bool = True) -> pathlib.Path:
    if not re.match(r"^[a-zA-Z0-9_-]+$", home_dir):
        raise HTTPException(status_code=400, detail="Invalid home directory name.")

    if home_dir not in user_manager.get_home_dirs(username):
        raise HTTPException(status_code=403, detail=f"Access to home directory '{home_dir}' denied.")

    base_dir = (pathlib.Path(settings.storage_path) / username / home_dir).resolve()

    if not base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Home directory not found.")

    # Normalize the sub_path to prevent traversal tricks like '.../'
    normalized_sub_path = os.path.normpath(sub_path).lstrip('/')
    if '..' in normalized_sub_path.split(os.path.sep):
        raise HTTPException(status_code=403, detail="Directory traversal attempt detected.")

    full_path = (base_dir / normalized_sub_path).resolve()

    if base_dir not in full_path.parents and full_path != base_dir:
        raise HTTPException(status_code=403, detail="Directory traversal attempt detected.")

    if check_existence and not full_path.exists():
        raise HTTPException(status_code=404, detail="Path not found.")

    return full_path

async def _perform_deletion(task_id: str, username: str, home_dir: str, paths_to_delete: List[str]):
    DELETION_TASKS[task_id]["status"] = "processing"
    deleted_count = 0
    try:
        for p in paths_to_delete:
            validated_path = _get_validated_path(username, home_dir, p)
            if validated_path.is_dir():
                await asyncio.to_thread(shutil.rmtree, validated_path)
            elif validated_path.is_file():
                await asyncio.to_thread(os.remove, validated_path)
            deleted_count += 1
        DELETION_TASKS[task_id].update({
            "status": "completed",
            "message": f"Successfully deleted {deleted_count} items."
        })
    except Exception as e:
        logger.error(f"Deletion task {task_id} failed: {e}")
        DELETION_TASKS[task_id].update({
            "status": "error",
            "message": "An error occurred during deletion."
        })

files_router = APIRouter(
    prefix="/api/files",
    dependencies=[Depends(verify_persistent_storage_enabled)],
    route_class=EncryptedRoute,
)

CHUNK_SIZE = 2 * 1024 * 1024

@files_router.get("/download/chunk/{home_dir}", response_model=FileChunkResponse)
async def download_file_chunk(
     home_dir: str,
    path: str = Query(...),
    chunk_index: int = Query(...),
    user: dict = Depends(verify_persistent_storage_enabled),
):
    validated_path = _get_validated_path(user["username"], home_dir, path)
    if not validated_path.is_file():
        raise HTTPException(status_code=404, detail="File not found or is a directory.")
 
    try:
        with open(validated_path, "rb") as f:
            f.seek(chunk_index * CHUNK_SIZE)
            chunk_data = f.read(CHUNK_SIZE)
            is_last_chunk = len(chunk_data) < CHUNK_SIZE
 
        return {
            "chunk_data_b64": base64.b64encode(chunk_data).decode('utf-8'),
            "is_last_chunk": is_last_chunk
        }
    except Exception as e:
        logger.error(f"Error reading chunk for file {path}: {e}")
        raise HTTPException(status_code=500, detail="Error reading file chunk.")

@files_router.post("/create_folder/{home_dir}", response_model=GenericSuccessMessage)
async def create_folder(
    home_dir: str,
    decrypted_body: dict = Depends(get_decrypted_request_body),
    user: dict = Depends(verify_persistent_storage_enabled),
):
    req = CreateFolderRequest(**decrypted_body)
    validated_path = _get_validated_path(user["username"], home_dir, req.path)
    new_folder_path = validated_path / req.folder_name
    if new_folder_path.exists():
        raise HTTPException(status_code=409, detail=f"Folder '{req.folder_name}' already exists.")
    try:
        new_folder_path.mkdir()
        return {"message": f"Folder '{req.folder_name}' created successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create folder: {e}")

@files_router.post("/delete/{home_dir}", response_model=DeleteTaskResponse)
async def initiate_deletion(
    home_dir: str,
    decrypted_body: dict = Depends(get_decrypted_request_body),
    user: dict = Depends(verify_persistent_storage_enabled),
):
    req = DeleteItemsRequest(**decrypted_body)
    task_id = str(uuid.uuid4())
    DELETION_TASKS[task_id] = {"status": "pending"}
    asyncio.create_task(_perform_deletion(task_id, user["username"], home_dir, req.paths))
    return {"message": "Deletion task started.", "task_id": task_id}

@files_router.get("/delete_status/{task_id}", response_model=DeleteStatusResponse)
async def check_deletion_status(task_id: str, user: dict = Depends(verify_token)):
    task = DELETION_TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task

@files_router.get("/list/{home_dir}", response_model=FileListResponse)
async def list_files(
    home_dir: str,
    path: str = Query("/"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: dict = Depends(verify_persistent_storage_enabled),
):
    validated_path = _get_validated_path(user["username"], home_dir, path)
    if not validated_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a valid directory.")

    try:
        all_items = sorted(
            validated_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
        )
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Error reading directory: {e}")

    start = (page - 1) * per_page
    end = start + per_page
    paginated_items = all_items[start:end]

    home_dir_root = (pathlib.Path(settings.storage_path) / user["username"] / home_dir)

    response_items = []
    for item in paginated_items:
        stat = item.stat()
        item_path = f"/{item.relative_to(home_dir_root)}".replace("\\", "/")
        if str(item.relative_to(home_dir_root)) == ".":
            item_path = "/"
        
        response_items.append(
            {
                "name": item.name,
                "path": item_path,
                "is_dir": item.is_dir(),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )

    return {
        "items": response_items,
        "total": len(all_items),
        "page": page,
        "per_page": per_page,
        "path": path,
    }

@files_router.post("/upload_to_dir/{home_dir}", response_model=GenericSuccessMessage)
async def finalize_upload_to_dir(
    home_dir: str,
    decrypted_body: dict = Depends(get_decrypted_request_body),
    user: dict = Depends(verify_persistent_storage_enabled),
):
    try:
        req = FinalizeUploadToDirRequest(**decrypted_body)

        dest_dir_path = _get_validated_path(user["username"], home_dir, req.path, check_existence=True)
        if not dest_dir_path.is_dir():
            raise HTTPException(status_code=400, detail="Destination path is not a valid directory.")

        reassembled_file_path = await _reassemble_file(
            req.upload_id, req.total_chunks, req.filename
        )

        safe_filename = os.path.basename(req.filename)
        actual_filename = await asyncio.to_thread(_get_unique_filename, str(dest_dir_path), safe_filename)
        final_location = dest_dir_path / actual_filename

        try:
            await asyncio.to_thread(shutil.move, reassembled_file_path, str(final_location))
            await asyncio.to_thread(os.chmod, str(final_location), 0o644)
            logger.info(f"User '{user['username']}' uploaded '{actual_filename}' to '{home_dir}{req.path}'")
            return {"message": "File uploaded successfully."}
        except Exception as e:
            if os.path.exists(reassembled_file_path):
                os.remove(reassembled_file_path)
            logger.error(f"Failed to move finalized upload for user '{user['username']}': {e}")
            raise HTTPException(status_code=500, detail="Could not place file in destination.")

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")

@files_router.post("/share", response_model=PublicShareInfo)
async def create_public_share(
    decrypted_body: dict = Depends(get_decrypted_request_body),
    user: dict = Depends(verify_public_sharing_enabled),
):
    try:
        req = ShareFileRequest(**decrypted_body)
        username = user["username"]
        
        source_path = _get_validated_path(username, req.home_dir, req.path)
        if not source_path.is_file():
            raise HTTPException(status_code=400, detail="Path does not point to a file.")

        share_id = str(uuid.uuid4())
        dest_path = os.path.join(settings.public_storage_path, share_id)
        
        await asyncio.to_thread(shutil.copy, source_path, dest_path)
        
        stat_info = source_path.stat()
        
        password_hash = None
        if req.password:
            password_hash = hashlib.sha256(req.password.encode()).hexdigest()
            
        expiry_timestamp = None
        if req.expiry_hours is not None and req.expiry_hours > 0:
            expiry_timestamp = time.time() + (req.expiry_hours * 3600)

        metadata = PublicShareMetadata(
            owner_username=username,
            original_filename=source_path.name,
            created_at=time.time(),
            size_bytes=stat_info.st_size,
            password_hash=password_hash,
            expiry_timestamp=expiry_timestamp,
        )
        
        PUBLIC_SHARES_METADATA[share_id] = metadata
        await save_public_shares_metadata()

        return PublicShareInfo(
            share_id=share_id,
            original_filename=metadata.original_filename,
            size_bytes=metadata.size_bytes,
            created_at=metadata.created_at,
            expiry_timestamp=metadata.expiry_timestamp,
            has_password=bool(metadata.password_hash),
            url=f"/public/{share_id}"
        )

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")
    except Exception as e:
        logger.error(f"Failed to create share for user '{user['username']}': {e}")
        raise HTTPException(status_code=500, detail="Failed to create share.")

@files_router.get("/shares", response_model=List[PublicShareInfo])
async def list_public_shares(user: dict = Depends(verify_public_sharing_enabled)):
    user_shares = [PublicShareInfo(share_id=sid, url=f"/public/{sid}", has_password=bool(meta.password_hash), **meta.dict()) for sid, meta in PUBLIC_SHARES_METADATA.items() if meta.owner_username == user["username"]]
    return sorted(user_shares, key=lambda s: s.created_at, reverse=True)

@files_router.delete("/share/{share_id}", status_code=204)
async def delete_public_share(share_id: str, user: dict = Depends(verify_public_sharing_enabled)):
    if (metadata := PUBLIC_SHARES_METADATA.get(share_id)) and metadata.owner_username == user["username"]:
        if os.path.exists(public_file_path := os.path.join(settings.public_storage_path, share_id)):
            try: os.remove(public_file_path)
            except OSError as e: logger.error(f"Error deleting share file for {share_id}: {e}")
        del PUBLIC_SHARES_METADATA[share_id]
        await save_public_shares_metadata()
        return Response(status_code=204)
    raise HTTPException(status_code=404 if not metadata else 403, detail="Share not found or permission denied.")

encrypted_router.include_router(admin_router)
encrypted_router.include_router(homedir_router)
encrypted_router.include_router(session_router)
encrypted_router.include_router(upload_router)
encrypted_router.include_router(files_router)
api_app.include_router(encrypted_router)

@api_app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def read_root():
    html_file_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(html_file_path):
        with open(html_file_path, 'r') as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>SealSkin Server</h1>", status_code=404)

@api_app.get("/sealskin.zip", include_in_schema=False)
async def download_zip():
    zip_file_path = "/sealskin.zip"
    if not os.path.exists(zip_file_path):
        raise HTTPException(status_code=404, detail="File not found at /sealskin.zip on the server filesystem.")
    return FileResponse(path=zip_file_path, media_type='application/zip', filename='sealskin.zip')
