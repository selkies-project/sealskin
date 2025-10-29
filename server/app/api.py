import base64
import json
import os
import shutil
import uuid
import time
import secrets
import yaml
import logging
import subprocess
import tempfile
import re
from typing import Callable, Dict, List, Optional
import asyncio
from contextlib import asynccontextmanager
import httpx
from collections import defaultdict
import docker
from docker.errors import DockerException

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Depends, HTTPException, Request, Response, APIRouter
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from jose import JWTError, jwt
from pydantic import ValidationError

from .settings import settings
from .models import *
from .providers.docker_provider import DockerProvider
from . import user_manager

logger = logging.getLogger(__name__)

SESSIONS_DB: Dict[str, Dict] = {}
CRYPTO_SESSIONS: Dict[str, bytes] = {}
INSTALLED_APPS: Dict[str, InstalledApp] = {}
APP_STORES: List[AppStore] = []
APP_TEMPLATES: Dict[str, Dict] = {}
PROVIDER_CACHE: Dict[str, DockerProvider] = {}
AVAILABLE_GPUS: List[Dict] = []
IMAGE_METADATA: Dict[str, Dict] = {}
PULL_STATUS: Dict[str, str] = {}
SYSTEM_STATS_CACHE: Dict[str, any] = {"data": None, "timestamp": 0}
CPU_MODEL: str = "Unknown"
PATH_PREFIX_MAP: Dict[str, str] = {}

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


async def _detect_docker_path_prefixes():
    """If running in Docker, inspects self to find host mount paths."""
    global PATH_PREFIX_MAP
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

        apps_using_image = [
            app
            for app in INSTALLED_APPS.values()
            if app.provider_config.image == image_name
        ]
        if apps_using_image:
            logger.info(
                f"Image '{image_name}' updated, checking autostart scripts for {len(apps_using_image)} app(s)."
            )
            for app in apps_using_image:
                await _update_autostart_cache(app)

        logger.info(f"Background pull for '{image_name}' completed successfully.")
    except Exception as e:
        logger.error(f"Background pull for image '{image_name}' failed: {e}")
    finally:
        PULL_STATUS.pop(image_name, None)


async def auto_update_app_images():
    while True:
        await asyncio.sleep(settings.auto_update_interval_seconds)
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
    await _detect_docker_path_prefixes()
    _get_cpu_model()
    user_manager.load_users_and_groups()
    load_app_configs()
    load_app_templates()
    detect_gpus()

    logger.info("Populating initial image metadata cache...")
    all_images = {app.provider_config.image for app in INSTALLED_APPS.values()}
    for image_name in all_images:
        await _get_and_cache_image_metadata(image_name)
    logger.info("Image metadata cache populated.")

    update_task = None
    if settings.auto_update_apps:
        update_task = asyncio.create_task(auto_update_app_images())
    yield
    logger.info("API server shutting down...")
    if update_task:
        update_task.cancel()
    try:
        if update_task:
            await update_task
    except asyncio.CancelledError:
        logger.info("Background tasks successfully cancelled.")


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

    final_env = {
        "SUBFOLDER": subfolder,
        "PUID": str(settings.puid),
        "PGID": str(settings.pgid),
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
    container_file_path = (
        os.path.join(settings.container_config_path, "Desktop", "files", filename)
        if filename
        else None
    )

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
    elif file_bytes and filename:
        host_mount_path = os.path.join(settings.storage_path, "sealskin_ephemeral", str(uuid.uuid4()))

    if app_config.provider_config.autostart:
        autostart_cache_path = _get_autostart_cache_path(app_config)
        if (
            autostart_cache_path
            and os.path.exists(autostart_cache_path)
            and os.path.getsize(autostart_cache_path) > 0
        ):
            if not host_mount_path:
                host_mount_path = os.path.join(settings.storage_path, "sealskin_ephemeral", str(uuid.uuid4()))
                logger.info(
                    f"[{session_id}] Created ephemeral storage for autostart script."
                )

            autostart_dir = os.path.join(host_mount_path, ".config", "openbox")
            autostart_path = os.path.join(autostart_dir, "autostart")
            try:
                os.makedirs(autostart_dir, exist_ok=True, mode=0o755)
                shutil.copy(autostart_cache_path, autostart_path)
                os.chmod(autostart_path, 0o755)
                logger.info(
                    f"[{session_id}] Successfully wrote autostart script for '{app_config.name}'"
                )
            except Exception as e:
                logger.error(f"[{session_id}] Failed to write autostart script: {e}")

    if host_mount_path:
        translated_host_mount_path = _translate_path_to_host(host_mount_path)
        volumes[translated_host_mount_path] = {
            "bind": settings.container_config_path,
            "mode": "rw",
        }
        if file_bytes and filename:
            file_dest_dir = os.path.join(host_mount_path, "Desktop", "files")
            os.makedirs(file_dest_dir, exist_ok=True, mode=0o755)
            file_location = os.path.join(file_dest_dir, filename)
            with open(file_location, "wb") as f:
                f.write(file_bytes)
            os.chmod(file_location, 0o644)
            if open_file_on_launch:
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
        }
        logger.info(
            f"[{session_id}] Session ready for {username}. Proxying to {instance_details['ip']}:{instance_details['port']}"
        )
        return {"session_url": f"/{session_id}/?access_token={access_token}"}
    except Exception as e:
        if host_mount_path and settings.upload_dir in host_mount_path:
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
        file_dest_dir = os.path.join(host_mount_path, "Desktop", "files")
        os.makedirs(file_dest_dir, exist_ok=True, mode=0o755)
        file_location = os.path.join(file_dest_dir, safe_filename)

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
            f"[{session_id}] User '{user['username']}' wrote file '{safe_filename}' to session via chunked upload."
        )
        return {
            "status": "success",
            "message": f"File '{safe_filename}' written to session.",
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
        "api_port": settings.api_port,
        "session_port": settings.session_port,
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
async def get_available_apps(url: str):
    async def fetch_and_process_store(content: str):
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
                if (
                    "provider_config" in app
                    and "extensions" in app["provider_config"]
                    and app["provider_config"]["extensions"]
                ):
                    original_extensions = app["provider_config"]["extensions"]
                    flattened_extensions = []
                    for item in original_extensions:
                        if isinstance(item, list):
                            flattened_extensions.extend(item)
                        else:
                            flattened_extensions.append(item)
                    app["provider_config"]["extensions"] = flattened_extensions

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
            return await fetch_and_process_store(response.text)
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

    cache_path = _get_autostart_cache_path(app_to_delete)
    if cache_path and os.path.exists(cache_path):
        try:
            os.remove(cache_path)
            logger.info(
                f"Deleted autostart cache file for app '{app_to_delete.name}' at {cache_path}"
            )
        except OSError as e:
            logger.error(f"Failed to delete autostart cache file {cache_path}: {e}")

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
            settings.storage_path, username, req.home_name, "Desktop", "files"
        )

        await asyncio.to_thread(os.makedirs, file_dest_dir, exist_ok=True, mode=0o755)

        file_location = os.path.join(file_dest_dir, safe_filename)

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
            f"User '{username}' uploaded file '{safe_filename}' to home directory '{req.home_name}'."
        )
        return {
            "status": "success",
            "message": f"File '{safe_filename}' uploaded successfully to '{req.home_name}'.",
        }

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {e}")


encrypted_router.include_router(admin_router)
encrypted_router.include_router(homedir_router)
encrypted_router.include_router(session_router)
encrypted_router.include_router(upload_router)
api_app.include_router(encrypted_router)
