"""Session launch orchestration.

`build_launch_spec` assembles everything a provider needs to start an
application container (environment, volumes, GPU, autostart script, Docker
overrides). It is used by the launch routes for new sessions and by the
collaboration module when a room swaps to another application, so the two
paths can no longer drift apart.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import secrets
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import docker
from fastapi import HTTPException

from . import config_store
from .docker_utils import translate_path_to_host
from .fsutil import safe_copytree, sanitize_for_filename, unique_filename
from .models import InstalledApp
from .providers.docker_provider import DockerProvider
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

DOCKER_LIST_KEYS = ("devices", "volumes")
DOCKER_DICT_KEYS = ("environment",)


@dataclass
class LaunchSpec:
    """Everything needed to call `provider.launch`.

    Attributes:
        env: Container environment.
        volumes: Docker volume mapping (host path -> bind spec).
        gpu_config: Selected GPU, or `None`.
        app_config: Resolved app dictionary with merged `docker_overrides`.
        host_mount_path: Server-side path mounted as the session home.
        shared_files_path: Server-side path mounted at `Desktop/files`.
        launch_context: What the session was opened with (URL or file).
        collaboration: Extra provider kwargs for collaboration sessions.
    """

    env: dict[str, str]
    volumes: dict[str, dict[str, str]]
    gpu_config: dict[str, Any] | None
    app_config: dict[str, Any]
    host_mount_path: str | None
    shared_files_path: str | None
    launch_context: dict[str, Any] | None = None
    collaboration: dict[str, Any] = field(default_factory=dict)

    def provider_kwargs(self, session_id: str) -> dict[str, Any]:
        """Return the keyword arguments for `provider.launch`."""
        kwargs: dict[str, Any] = {
            "session_id": session_id,
            "env_vars": self.env,
            "volumes": self.volumes,
            "gpu_config": self.gpu_config,
            "network": state.discovered_network,
        }
        kwargs.update(self.collaboration)
        return kwargs


def ephemeral_base() -> str:
    """Return the directory that holds ephemeral (cleanroom) session storage."""
    return os.path.join(settings.storage_path, "sealskin_ephemeral")


def new_ephemeral_dir(suffix: str = "") -> str:
    """Create and return a fresh ephemeral directory."""
    path = os.path.join(ephemeral_base(), f"{uuid.uuid4()}{suffix}")
    os.makedirs(path, exist_ok=True, mode=0o700)
    return path


def extract_docker_overrides(settings_dict: dict[str, Any]) -> dict[str, Any]:
    """Translate `DOCKER_*` template settings into Docker SDK run options.

    Args:
        settings_dict: Template settings (environment variable names to values).

    Returns:
        Keyword arguments understood by `containers.run`.
    """
    overrides: dict[str, Any] = {}

    def parse_list(val: str) -> list[str]:
        return [x.strip() for x in str(val).split(",") if x.strip()]

    def parse_kv_list(val: str, separator: str = "=") -> dict[str, str]:
        res: dict[str, str] = {}
        for item in str(val).split(","):
            if separator in item:
                k, v = item.split(separator, 1)
                res[k.strip()] = v.strip()
        return res

    for key, raw in settings_dict.items():
        if not key.startswith("DOCKER_"):
            continue
        val = str(raw).strip()
        if not val:
            continue
        if key == "DOCKER_PRIVILEGED":
            overrides["privileged"] = val.lower() == "true"
        elif key == "DOCKER_CAP_ADD":
            overrides["cap_add"] = parse_list(val)
        elif key == "DOCKER_CAP_DROP":
            overrides["cap_drop"] = parse_list(val)
        elif key == "DOCKER_SECURITY_OPT":
            overrides["security_opt"] = parse_list(val)
        elif key == "DOCKER_DEVICES":
            overrides["devices"] = parse_list(val)
        elif key == "DOCKER_DNS":
            overrides["dns"] = parse_list(val)
        elif key == "DOCKER_SHM_SIZE":
            overrides["shm_size"] = val
        elif key == "DOCKER_MEM_LIMIT":
            overrides["mem_limit"] = val
        elif key == "DOCKER_CPU_SHARES":
            try:
                overrides["cpu_shares"] = int(val)
            except ValueError:
                pass
        elif key == "DOCKER_NANO_CPUS":
            try:
                overrides["nano_cpus"] = int(val)
            except ValueError:
                pass
        elif key == "DOCKER_NETWORK_MODE":
            overrides["network_mode"] = val
        elif key == "DOCKER_IPC_MODE":
            overrides["ipc_mode"] = val
        elif key == "DOCKER_PID_MODE":
            overrides["pid_mode"] = val
        elif key == "DOCKER_GROUP_ADD":
            overrides["group_add"] = parse_list(val)
        elif key == "DOCKER_EXTRA_HOSTS":
            overrides["extra_hosts"] = parse_kv_list(val, ":")
        elif key == "DOCKER_SYSCTLS":
            overrides["sysctls"] = parse_kv_list(val, "=")
        elif key == "DOCKER_ULIMITS":
            ulimits = []
            for item in val.split(","):
                if "=" not in item:
                    continue
                name, limit = item.split("=", 1)
                try:
                    if ":" in limit:
                        soft, hard = limit.split(":", 1)
                        ulimits.append(
                            docker.types.Ulimit(name=name.strip(), soft=int(soft), hard=int(hard))
                        )
                    else:
                        ulimits.append(
                            docker.types.Ulimit(name=name.strip(), soft=int(limit), hard=int(limit))
                        )
                except ValueError:
                    continue
            if ulimits:
                overrides["ulimits"] = ulimits
        elif key == "DOCKER_TMPFS":
            overrides["tmpfs"] = parse_kv_list(val, ":")
        elif key == "DOCKER_BIND_MOUNTS":
            overrides["volumes"] = parse_list(val)
        elif key == "DOCKER_ENV":
            overrides["environment"] = parse_kv_list(val, "=")
    return overrides


def merge_docker_overrides(
    existing: dict[str, Any] | None, template_overrides: dict[str, Any]
) -> dict[str, Any]:
    """Combine store/app Docker overrides with template-derived ones.

    Lists (devices, volumes) are concatenated and `environment` dictionaries
    merged; any other key from the template replaces the existing value.

    Args:
        existing: Overrides carried by the app definition.
        template_overrides: Overrides derived from `DOCKER_*` settings.

    Returns:
        The merged overrides.
    """
    merged: dict[str, Any] = dict(existing or {})
    for key, value in template_overrides.items():
        if key in DOCKER_LIST_KEYS and isinstance(merged.get(key), list) and isinstance(value, list):
            merged[key] = list(merged[key]) + list(value)
        elif key in DOCKER_DICT_KEYS and isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def validate_gpu(
    selected_gpu: str | None, effective_settings: dict[str, Any], app: InstalledApp
) -> dict[str, Any] | None:
    """Validate a GPU selection for a launch.

    Args:
        selected_gpu: Device path chosen by the user, or `None`.
        effective_settings: The user's effective settings.
        app: Application being launched.

    Returns:
        The GPU descriptor from `state.available_gpus`, or `None`.

    Raises:
        HTTPException: 400 when the GPU is unavailable or unsupported.
    """
    if not selected_gpu or not effective_settings.get("gpu", False):
        return None
    gpu_info = next((g for g in state.available_gpus if g["device"] == selected_gpu), None)
    if not gpu_info:
        raise HTTPException(status_code=400, detail=f"Selected GPU '{selected_gpu}' is not available.")
    if gpu_info["type"] == "nvidia" and not app.provider_config.nvidia_support:
        raise HTTPException(status_code=400, detail=f"App '{app.name}' does not support Nvidia GPUs.")
    if gpu_info["type"] == "dri3" and not app.provider_config.dri3_support:
        raise HTTPException(status_code=400, detail=f"App '{app.name}' does not support DRI3 GPUs.")
    return gpu_info


def gpu_for_app(gpu_config: dict[str, Any] | None, app: InstalledApp) -> dict[str, Any] | None:
    """Return `gpu_config` if `app` supports it, otherwise `None`.

    Used when a collaboration room swaps applications and the session's GPU
    may not be usable by the new app.
    """
    if not gpu_config:
        return None
    if gpu_config["type"] == "nvidia" and not app.provider_config.nvidia_support:
        return None
    if gpu_config["type"] == "dri3" and not app.provider_config.dri3_support:
        return None
    return gpu_config


def _autostart_content(app: InstalledApp, wayland_mode: bool, session_id: str) -> str | None:
    """Return the autostart script for an app, custom first then repository cache."""
    provider_config = app.provider_config
    custom = (
        provider_config.custom_autostart_wayland_script_b64
        if wayland_mode
        else provider_config.custom_autostart_script_b64
    )
    flavour = "Wayland " if wayland_mode else ""
    if custom:
        try:
            content = base64.b64decode(custom).decode("utf-8")
            logger.info("[%s] Using custom %sautostart script for '%s'.", session_id, flavour, app.name)
            return content
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Failed to decode custom %sautostart script: %s", session_id, flavour, exc)
    if provider_config.autostart:
        cache_path = config_store.autostart_cache_path(app, "-wayland" if wayland_mode else "")
        if cache_path and os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                with open(cache_path, encoding="utf-8") as handle:
                    content = handle.read()
                logger.info(
                    "[%s] Using cached repository %sautostart script for '%s'.",
                    session_id,
                    flavour,
                    app.name,
                )
                return content
            except OSError as exc:
                logger.error("[%s] Failed to read cached %sautostart script: %s", session_id, flavour, exc)
    return None


def write_autostart(app: InstalledApp, host_mount_path: str, wayland_mode: bool, session_id: str) -> None:
    """Write the app's autostart script into the session home, if any.

    Args:
        app: Application being launched.
        host_mount_path: Session home on the server.
        wayland_mode: Select the labwc (Wayland) or openbox script.
        session_id: Session id for log prefixes.
    """
    content = _autostart_content(app, wayland_mode, session_id)
    if not content:
        return
    subpath = (
        os.path.join(".config", "labwc", "autostart")
        if wayland_mode
        else os.path.join(".config", "openbox", "autostart")
    )
    autostart_file = os.path.join(host_mount_path, subpath)
    try:
        os.makedirs(os.path.dirname(autostart_file), exist_ok=True, mode=0o755)
        with open(autostart_file, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(autostart_file, 0o755)
        logger.info("[%s] Wrote autostart script to session storage.", session_id)
    except OSError as exc:
        logger.error("[%s] Failed to write autostart script: %s", session_id, exc)


def collaboration_initial_tokens(session: dict[str, Any]) -> dict[str, Any]:
    """Build the token table pushed to a collaboration container.

    Args:
        session: Session record with `controller_token`, `viewers` and
            `mk_owner_token`.

    Returns:
        Mapping of token to `{"role", "slot", "mk_control"}`.
    """
    mk_owner = session.get("mk_owner_token")
    controller_token = session.get("controller_token")
    tokens: dict[str, Any] = {
        controller_token: {
            "role": "controller",
            "slot": session.get("controller_slot"),
            "mk_control": (mk_owner == controller_token) if mk_owner else True,
        }
    }
    for viewer in session.get("viewers", []):
        tokens[viewer["token"]] = {
            "role": "viewer",
            "slot": viewer.get("slot"),
            "mk_control": viewer["token"] == mk_owner,
        }
    return tokens


def build_launch_spec(
    app: InstalledApp,
    session_id: str,
    *,
    base_env: dict[str, str],
    extra_env: dict[str, str] | None,
    language: str | None,
    wayland_mode: bool,
    gpu_config: dict[str, Any] | None,
    host_mount_path: str | None,
    shared_files_path: str | None,
    collaboration: dict[str, Any] | None = None,
) -> LaunchSpec:
    """Assemble the environment, volumes and Docker options for a launch.

    Args:
        app: Resolved application.
        session_id: Session id for log prefixes.
        base_env: Session-level variables (SUBFOLDER, credentials, ...).
        extra_env: Request-level variables such as `SEALSKIN_URL`.
        language: Locale for `LC_ALL` (ignored for the default English).
        wayland_mode: Whether the session runs the Wayland compositor.
        gpu_config: Validated GPU descriptor or `None`.
        host_mount_path: Session home directory on the server, or `None`.
        shared_files_path: Shared files directory on the server, or `None`.
        collaboration: Extra provider kwargs for collaboration sessions.

    Returns:
        A `LaunchSpec`.
    """
    env: dict[str, str] = dict(base_env)
    if wayland_mode:
        env["PIXELFLUX_WAYLAND"] = "true"
    else:
        env.setdefault("PIXELFLUX_WAYLAND", "false")

    template = state.app_templates.get(app.app_template)
    template_settings: dict[str, Any] = {}
    if template and template.get("settings"):
        template_settings = template["settings"]
        env.update({k: str(v) for k, v in template_settings.items() if not k.startswith("DOCKER_")})
    elif not template:
        logger.warning(
            "[%s] Template '%s' not found for app '%s'. Using container defaults.",
            session_id,
            app.app_template,
            app.name,
        )

    launch_context: dict[str, Any] | None = None
    if extra_env:
        env.update(extra_env)
    if "SEALSKIN_URL" in env:
        launch_context = {"type": "url", "value": env["SEALSKIN_URL"]}
    if language and language.lower() != "en_us.utf-8":
        env["LC_ALL"] = language
    for override in app.provider_config.env or []:
        env[override.name] = override.value

    if gpu_config and (gpu_config["type"] == "dri3" or (gpu_config["type"] == "nvidia" and wayland_mode)):
        env["DRI_NODE"] = gpu_config["device"]
        env["DRINODE"] = gpu_config["device"]

    app_config = app.model_dump()
    template_overrides = extract_docker_overrides(template_settings)
    if template_overrides:
        provider_config = app_config.setdefault("provider_config", {})
        provider_config["docker_overrides"] = merge_docker_overrides(
            provider_config.get("docker_overrides"), template_overrides
        )

    if host_mount_path:
        write_autostart(app, host_mount_path, wayland_mode, session_id)

    volumes: dict[str, dict[str, str]] = {}
    if host_mount_path:
        volumes[translate_path_to_host(host_mount_path)] = {
            "bind": settings.container_config_path,
            "mode": "rw",
        }
    if shared_files_path:
        os.makedirs(shared_files_path, exist_ok=True, mode=0o755)
        if host_mount_path:
            os.makedirs(os.path.join(host_mount_path, "Desktop", "files"), exist_ok=True, mode=0o755)
        volumes[translate_path_to_host(shared_files_path)] = {
            "bind": os.path.join(settings.container_config_path, "Desktop", "files"),
            "mode": "rw",
        }

    return LaunchSpec(
        env=env,
        volumes=volumes,
        gpu_config=gpu_config,
        app_config=app_config,
        host_mount_path=host_mount_path,
        shared_files_path=shared_files_path,
        launch_context=launch_context,
        collaboration=dict(collaboration or {}),
    )


_TZ_NAME_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}$")
DEFAULT_TIMEZONE = "Etc/UTC"


def is_valid_timezone(name: Any) -> bool:
    """Whether `name` looks like an IANA zone name (`Europe/Berlin`, `UTC`)."""
    return isinstance(name, str) and 0 < len(name) <= 64 and bool(_TZ_NAME_RE.match(name))


def resolve_timezone(*candidates: Any) -> str:
    """Pick the `TZ` for a container.

    The first candidate that looks like an IANA zone name wins. Candidates are
    the browser's reported zone and, for containers started inside an existing
    session, the zone the session was created with. With no usable candidate
    the server's own `TZ` applies, and failing that `Etc/UTC`.
    """
    for candidate in candidates:
        if is_valid_timezone(candidate):
            return candidate
    server_tz = os.environ.get("TZ")
    return server_tz if is_valid_timezone(server_tz) else DEFAULT_TIMEZONE


def session_base_env(
    session_id: str,
    custom_user: str,
    password: str,
    master_token: str | None,
    timezone: str | None = None,
) -> dict[str, str]:
    """Return the session-level environment shared by every container of a session.

    Args:
        session_id: The session; sets `SUBFOLDER`.
        custom_user: Basic-auth user for the container.
        password: Basic-auth password for the container.
        master_token: Collaboration master token, or `None` outside a room.
        timezone: IANA zone for `TZ`; resolved via `resolve_timezone`.
    """
    env = {
        "SUBFOLDER": f"/{session_id}/",
        "PUID": str(settings.puid),
        "PGID": str(settings.pgid),
        "CUSTOM_USER": custom_user,
        "PASSWORD": password,
        "TZ": resolve_timezone(timezone),
        "SELKIES_ALLOWED_ORIGINS": "*",
    }
    if master_token:
        env["SELKIES_MASTER_TOKEN"] = master_token
    return env


async def _resolve_storage(
    app: InstalledApp,
    session_id: str,
    username: str,
    effective_settings: dict[str, Any],
    home_name: str | None,
    forced_rw_mount: str | None,
) -> tuple[str | None, str | None]:
    """Decide the home and shared-files directories of a new session.

    Returns:
        `(host_mount_path, shared_files_path)`.

    Raises:
        HTTPException: When a requested home directory or template is missing.
    """
    persistent_allowed = effective_settings.get("persistent_storage", False)
    if forced_rw_mount:
        os.makedirs(forced_rw_mount, exist_ok=True, mode=0o700)
        return forced_rw_mount, None

    if app.is_meta_app:
        template_path = os.path.join(settings.home_templates_path, app.home_template_name or "")
        if not app.home_template_name or not os.path.isdir(template_path):
            raise HTTPException(
                status_code=500,
                detail=f"Home directory template for meta app '{app.name}' not found on server.",
            )
        is_persistent = persistent_allowed and (home_name is None or home_name.lower() != "cleanroom")
        if is_persistent:
            host_mount_path = os.path.join(
                settings.storage_path, username, f"auto-{sanitize_for_filename(app.name)}"
            )
            if not os.path.exists(host_mount_path):
                logger.info(
                    "[%s] First launch for meta-app. Copying template '%s' for user '%s'.",
                    session_id,
                    app.home_template_name,
                    username,
                )
                await asyncio.to_thread(safe_copytree, template_path, host_mount_path, True)
            return host_mount_path, os.path.join(settings.storage_path, username, "_sealskin_shared_files")
        host_mount_path = os.path.join(ephemeral_base(), str(uuid.uuid4()))
        logger.info(
            "[%s] Launching meta-app in cleanroom mode. Copying template '%s' to ephemeral storage.",
            session_id,
            app.home_template_name,
        )
        await asyncio.to_thread(safe_copytree, template_path, host_mount_path, True)
        return host_mount_path, new_ephemeral_dir("_shared")

    use_persistent = persistent_allowed and app.home_directories
    if not use_persistent:
        home_name = "cleanroom"
    if home_name and home_name.lower() != "cleanroom":
        host_mount_path = os.path.abspath(os.path.join(settings.storage_path, username, home_name))
        if not os.path.isdir(host_mount_path):
            raise HTTPException(status_code=404, detail=f"Home directory '{home_name}' not found.")
        shared = os.path.abspath(os.path.join(settings.storage_path, username, "_sealskin_shared_files"))
        return host_mount_path, shared
    return new_ephemeral_dir(), new_ephemeral_dir("_shared")


async def launch_application(
    application_id: str,
    username: str,
    effective_settings: dict[str, Any],
    home_name: str | None,
    env_vars: dict[str, str],
    language: str | None,
    selected_gpu: str | None,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    open_file_on_launch: bool = True,
    forced_rw_mount: str | None = None,
    launch_in_room_mode: bool = False,
    wayland_mode: bool = True,
    timezone: str | None = None,
) -> dict[str, str]:
    """Start a new session for a user.

    Args:
        application_id: Installed app id.
        username: Owner of the session.
        effective_settings: The user's effective settings.
        home_name: Home directory to mount, `"cleanroom"` or `None`.
        env_vars: Request-specific environment (`SEALSKIN_URL` / `SEALSKIN_FILE`).
        language: Locale for the session.
        selected_gpu: GPU device path or `None`.
        file_bytes: Contents of an uploaded file to place in the session.
        filename: Name of that file.
        open_file_on_launch: Whether to open the file automatically.
        forced_rw_mount: Mount this directory as the home (meta-app editing).
        launch_in_room_mode: Start a collaboration session.
        wayland_mode: Use the Wayland compositor.
        timezone: The browser's IANA zone; the container's `TZ` (see
            `resolve_timezone`).

    Returns:
        `{"session_url": str, "session_id": str}`.

    Raises:
        HTTPException: On validation failures or provider errors.
    """
    app = state.installed_apps.get(application_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"Application with ID '{application_id}' not found.")

    session_id = str(uuid.uuid4())
    access_token = secrets.token_urlsafe(32)
    master_token = controller_token = viewer_token = None
    if launch_in_room_mode:
        master_token = secrets.token_urlsafe(32)
        controller_token = secrets.token_urlsafe(16)
        viewer_token = secrets.token_urlsafe(16)
    custom_user = str(uuid.uuid4())
    password = str(uuid.uuid4())
    timezone = resolve_timezone(timezone)

    host_mount_path, shared_files_path = await _resolve_storage(
        app, session_id, username, effective_settings, home_name, forced_rw_mount
    )
    gpu_config = validate_gpu(selected_gpu, effective_settings, app)

    collaboration: dict[str, Any] = {}
    if launch_in_room_mode:
        collaboration = {
            "is_collaboration": True,
            "master_token": master_token,
            "initial_tokens": {controller_token: {"role": "controller", "slot": None}},
        }

    spec = build_launch_spec(
        app,
        session_id,
        base_env=session_base_env(session_id, custom_user, password, master_token, timezone),
        extra_env=env_vars,
        language=language,
        wayland_mode=wayland_mode,
        gpu_config=gpu_config,
        host_mount_path=host_mount_path,
        shared_files_path=shared_files_path,
        collaboration=collaboration,
    )

    launch_context = spec.launch_context
    if file_bytes is not None and filename and shared_files_path:
        actual_filename = unique_filename(shared_files_path, filename)
        file_location = os.path.join(shared_files_path, actual_filename)
        with open(file_location, "wb") as handle:
            handle.write(file_bytes)
        os.chmod(file_location, 0o644)
        if open_file_on_launch:
            spec.env["SEALSKIN_FILE"] = os.path.join(
                settings.container_config_path, "Desktop", "files", actual_filename
            )
            launch_context = {"type": "file", "value": filename}

    try:
        provider = DockerProvider(spec.app_config)
        instance = await provider.launch(**spec.provider_kwargs(session_id))
        now = time.time()
        session: dict[str, Any] = {
            "instance_id": instance["instance_id"],
            "ip": instance["ip"],
            "port": instance["port"],
            "created_at": now,
            "access_token": access_token,
            "provider_app_id": application_id,
            "username": username,
            "app_name": app.name,
            "app_logo": app.logo,
            "host_mount_path": host_mount_path,
            "shared_files_path": shared_files_path,
            "launch_context": launch_context,
            "custom_user": custom_user,
            "password": password,
            "gpu_config": gpu_config,
            "wayland_mode": wayland_mode,
            "timezone": timezone,
            "container_registry": {
                application_id: {
                    "instance_id": instance["instance_id"],
                    "ip": instance["ip"],
                    "port": instance["port"],
                    "app_id": application_id,
                    "created_at": now,
                }
            },
        }
        if launch_in_room_mode:
            session.update(
                {
                    "is_collaboration": True,
                    "master_token": master_token,
                    "controller_token": controller_token,
                    "viewer_token": viewer_token,
                    "viewers": [],
                }
            )
        async with state.sessions_lock:
            state.sessions[session_id] = session
        await config_store.save_sessions()

        logger.info(
            "[%s] Session ready for %s. Proxying to %s:%s",
            session_id,
            username,
            instance["ip"],
            instance["port"],
        )
        if launch_in_room_mode:
            session_url = f"/room/{session_id}?access_token={access_token}&token={controller_token}"
        else:
            session_url = f"/{session_id}/?access_token={access_token}"
        return {"session_url": session_url, "session_id": session_id}
    except Exception as exc:
        for path in (host_mount_path, shared_files_path):
            if path and path.startswith(ephemeral_base()):
                shutil.rmtree(path, ignore_errors=True)
        logger.error(
            "[%s] Unhandled exception during launch for app '%s': %s",
            session_id,
            application_id,
            exc,
            exc_info=True,
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=500, detail="An internal error occurred during application launch."
        ) from exc


async def ensure_container_for_session(
    session_id: str, target_app_id: str, timezone: str | None = None
) -> dict[str, Any]:
    """Start (or reuse) a container for another app inside an existing session.

    Used by collaboration rooms to swap between applications.

    Args:
        session_id: The session.
        target_app_id: Installed app id to start.
        timezone: IANA zone of the browser requesting the launch. Falls back
            to the zone the session was created with.

    Returns:
        The container registry entry for the app.

    Raises:
        HTTPException: If the session or app is unknown.
    """
    session = state.sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    registry = session.setdefault("container_registry", {})
    if target_app_id in registry:
        return registry[target_app_id]
    app = state.installed_apps.get(target_app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {target_app_id} not found.")

    wayland_mode = session.get("wayland_mode", True)
    current_home = session.get("host_mount_path")
    if current_home and current_home.startswith(ephemeral_base()):
        new_home: str | None = new_ephemeral_dir()
    else:
        username = session.get("username")
        new_home = None
        if username:
            new_home = os.path.join(
                settings.storage_path, username, f"auto-{sanitize_for_filename(app.name)}"
            )
            if app.is_meta_app and app.home_template_name and not os.path.exists(new_home):
                template_path = os.path.join(settings.home_templates_path, app.home_template_name)
                if os.path.isdir(template_path):
                    await asyncio.to_thread(safe_copytree, template_path, new_home, True)
            os.makedirs(new_home, exist_ok=True, mode=0o700)
    session["host_mount_path"] = new_home

    shared_files_path = session.get("shared_files_path")
    if shared_files_path and not os.path.exists(shared_files_path):
        shared_files_path = None

    collaboration: dict[str, Any] = {}
    if session.get("is_collaboration"):
        collaboration = {
            "is_collaboration": True,
            "master_token": session.get("master_token"),
            "initial_tokens": collaboration_initial_tokens(session),
        }

    spec = build_launch_spec(
        app,
        session_id,
        base_env=session_base_env(
            session_id,
            session.get("custom_user", "abc"),
            session.get("password", "abc"),
            session.get("master_token") if session.get("is_collaboration") else None,
            resolve_timezone(timezone, session.get("timezone")),
        ),
        extra_env=None,
        language=None,
        wayland_mode=wayland_mode,
        gpu_config=gpu_for_app(session.get("gpu_config"), app),
        host_mount_path=new_home,
        shared_files_path=shared_files_path,
        collaboration=collaboration,
    )

    provider = DockerProvider(spec.app_config)
    instance = await provider.launch(**spec.provider_kwargs(session_id))
    container_info = {
        "instance_id": instance["instance_id"],
        "ip": instance["ip"],
        "port": instance["port"],
        "app_id": target_app_id,
        "created_at": time.time(),
    }
    registry[target_app_id] = container_info
    await config_store.save_sessions()
    return container_info


async def stop_container_in_session(session_id: str, target_app_id: str) -> None:
    """Stop one app's container inside a session without ending the session."""
    session = state.sessions.get(session_id)
    if not session or "container_registry" not in session:
        return
    container_info = session["container_registry"].get(target_app_id)
    if not container_info:
        return
    app = state.installed_apps.get(target_app_id)
    if app:
        await DockerProvider(app.model_dump()).stop(container_info["instance_id"])
    del session["container_registry"][target_app_id]
    await config_store.save_sessions()


async def stop_session(session_id: str) -> None:
    """Stop every container of a session and remove its ephemeral storage.

    Args:
        session_id: The session to stop.
    """
    from . import collaboration

    logger.info("[%s] Stopping session...", session_id)
    try:
        await collaboration.notify_session_ended(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] Failed to broadcast session end: %s", session_id, exc)

    async with state.sessions_lock:
        session = state.sessions.pop(session_id, None)
    if not session:
        logger.warning("Attempted to stop session %s, but it was not found in the database.", session_id)
        return

    registry = session.get("container_registry") or {}
    if not registry and "provider_app_id" in session:
        registry = {session["provider_app_id"]: {"instance_id": session["instance_id"]}}
    for app_id, container_info in registry.items():
        app = state.installed_apps.get(app_id)
        if not app:
            continue
        try:
            await DockerProvider(app.model_dump()).stop(container_info["instance_id"])
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Failed to stop container for app %s: %s", session_id, app_id, exc)

    await config_store.save_sessions()
    for key in ("host_mount_path", "shared_files_path"):
        path = session.get(key)
        if path and path.startswith(ephemeral_base()) and os.path.exists(path):
            await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
    logger.info("[%s] Session stopped and cleaned up successfully.", session_id)
