"""Administrator endpoints: users, groups, stores, installed apps, templates."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import uuid
from collections import defaultdict
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from .. import config_store, user_manager
from ..docker_utils import get_and_cache_image_metadata, get_system_stats, pull_and_cache_image
from ..fsutil import safe_rmtree
from ..launch import launch_application, stop_session
from ..models import (
    AdminStatusResponse,
    AppStore,
    AppTemplate,
    AvailableApp,
    CreateAdminRequest,
    CreateGroupRequest,
    CreateMetaAppRequest,
    CreateUserRequest,
    CreateUserResponse,
    GPUInfo,
    Group,
    HomeDirectoryCreate,
    HomeDirectoryList,
    ImagePullResponse,
    ImageUpdateCheckResponse,
    InstalledApp,
    InstalledAppRecord,
    InstalledAppWithStatus,
    LaunchMetaCustomizeRequest,
    LaunchResponse,
    ManagementDataResponse,
    UpdateGroupRequest,
    UpdateUserRequest,
    User,
    UserSessionList,
)
from ..providers.docker_provider import DockerProvider
from ..security import (
    EncryptedRoute,
    get_decrypted_request_body,
    proxy_cert_not_after,
    verify_admin,
    verify_token,
)
from ..settings import settings
from ..state import state
from .sessions import session_info

logger = logging.getLogger(__name__)

status_router = APIRouter(route_class=EncryptedRoute)
router = APIRouter(
    prefix="/api/admin",
    dependencies=[Depends(verify_admin)],
    route_class=EncryptedRoute,
)


def _gpu_list() -> list[GPUInfo]:
    """Return the detected GPUs as API models."""
    return [GPUInfo(device=gpu["device"], driver=gpu["driver"]) for gpu in state.available_gpus]


@status_router.post("/api/admin/status", response_model=AdminStatusResponse)
async def admin_status(user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """Return the caller's role, settings and host statistics."""
    response: dict[str, Any] = {
        "is_admin": user.get("is_admin", False),
        "username": user.get("username"),
        "settings": user.get("effective_settings"),
        "gpus": [],
        "proxy_cert_expires_at": proxy_cert_not_after(settings.proxy_cert_path),
        **get_system_stats(),
    }
    if user.get("effective_settings", {}).get("gpu", False):
        response["gpus"] = _gpu_list()
    return response


@router.post("/data", response_model=ManagementDataResponse)
async def get_management_data() -> dict[str, Any]:
    """Return users, groups and server details for the dashboard."""
    return {
        "admins": user_manager.get_all_admins(),
        "users": user_manager.get_all_users(),
        "groups": user_manager.get_all_groups(),
        "server_public_key": state.server_public_key_pem,
        "api_port": state.discovered_api_port,
        "session_port": state.discovered_session_port,
        "gpus": _gpu_list(),
    }


# --- App stores ------------------------------------------------------------


def _require_safe_name(name: str, kind: str) -> str:
    """Validate a store or template name for use in file paths."""
    if not config_store.is_safe_name(name):
        raise HTTPException(status_code=400, detail=f"Invalid {kind} name.")
    return name


@router.get("/apps/stores", response_model=list[AppStore])
async def get_app_stores() -> list[AppStore]:
    """List configured app stores."""
    return state.app_stores


@router.post("/apps/stores", response_model=AppStore, status_code=201)
async def add_app_store(decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)) -> AppStore:
    """Add an app store."""
    try:
        store = AppStore(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _require_safe_name(store.name, "app store")
    if config_store.get_store(store.name):
        raise HTTPException(status_code=409, detail=f"App store with name '{store.name}' already exists.")
    state.app_stores.append(store)
    await config_store.save_app_stores()
    return store


@router.delete("/apps/stores/{store_name}", status_code=204)
async def delete_app_store(store_name: str) -> Response:
    """Remove an app store."""
    store = config_store.get_store(store_name)
    if not store:
        raise HTTPException(status_code=404, detail="App store not found.")
    state.app_stores.remove(store)
    await config_store.save_app_stores()
    return Response(status_code=204)


@router.get("/apps/available", response_model=list[AvailableApp])
async def get_available_apps(url: str, store_name: str, refresh: bool = False) -> list[dict[str, Any]]:
    """Return the apps published by a store (cached unless ``refresh``)."""
    _require_safe_name(store_name, "app store")
    try:
        return await config_store.fetch_store_apps(store_name, url, refresh)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=400, detail=f"Failed to fetch app store from URL '{url}': {exc}"
        ) from exc
    except (yaml.YAMLError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse app store YAML: {exc}") from exc


# --- Installed apps --------------------------------------------------------


def _save_logo(app_id: str, logo: str | None) -> str | None:
    """Persist a base64 logo as a PNG icon and return the icon URL.

    Args:
        app_id: App the icon belongs to.
        logo: Base64 image data, an existing URL, or ``None``.

    Returns:
        ``/api/app_icon/<id>`` when data was saved, otherwise ``logo`` unchanged.
    """
    if not logo or logo.startswith("/api/app_icon/") or logo.startswith("http"):
        return logo
    try:
        icon_data = base64.b64decode(logo, validate=True)
    except (ValueError, TypeError, binascii.Error):
        return logo
    os.makedirs(settings.app_icons_path, exist_ok=True, mode=0o700)
    icon_path = os.path.join(settings.app_icons_path, f"{app_id}.png")
    with open(icon_path, "wb") as handle:
        handle.write(icon_data)
    logger.info("Saved custom icon for app %s", app_id)
    return f"/api/app_icon/{app_id}"


def _normalise_scripts(provider_config: dict[str, Any]) -> None:
    """Turn empty autostart script strings into ``None`` in place."""
    for key in ("custom_autostart_script_b64", "custom_autostart_wayland_script_b64"):
        if provider_config.get(key) == "":
            provider_config[key] = None


def _with_status(app: InstalledApp) -> InstalledAppWithStatus:
    """Attach image metadata to an installed app."""
    metadata = state.image_metadata.get(app.provider_config.image, {})
    return InstalledAppWithStatus(
        **app.model_dump(),
        image_sha=metadata.get("sha"),
        last_checked_at=metadata.get("last_checked_at"),
        pull_status=state.pull_status.get(app.provider_config.image),
    )


@router.get("/apps/installed", response_model=list[InstalledAppWithStatus])
async def list_installed_apps() -> list[InstalledAppWithStatus]:
    """List installed apps with image status."""
    apps = [_with_status(app) for app in state.installed_apps.values()]
    return sorted(apps, key=lambda a: a.name.lower())


@router.post("/apps/installed", response_model=InstalledApp, status_code=201)
async def install_app(decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)) -> InstalledApp:
    """Install an app from a store entry sent by the admin UI."""
    try:
        app = InstalledApp(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if app.id in state.installed_records:
        raise HTTPException(status_code=409, detail="App with this ID already exists.")
    app.logo = _save_logo(app.id, app.logo) or app.logo
    record = config_store.record_from_app(app)
    resolved = config_store.set_record(record)
    await config_store.save_installed_apps()
    if not resolved:
        raise HTTPException(status_code=422, detail="Application could not be resolved against its store.")
    asyncio.create_task(pull_and_cache_image(resolved.provider_config.image))
    return resolved


@router.put("/apps/installed/{app_id}", response_model=InstalledApp)
async def update_installed_app(
    app_id: str, decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)
) -> InstalledApp:
    """Replace an installed app with a full definition."""
    try:
        app_update = InstalledApp(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if app_id not in state.installed_records:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    if app_id != app_update.id:
        raise HTTPException(status_code=400, detail="App ID in path does not match body.")

    app_update.logo = _save_logo(app_id, app_update.logo) or app_update.logo
    provider_config = app_update.provider_config
    if provider_config.custom_autostart_script_b64 == "":
        provider_config.custom_autostart_script_b64 = None
    if provider_config.custom_autostart_wayland_script_b64 == "":
        provider_config.custom_autostart_wayland_script_b64 = None

    old_app = state.installed_apps.get(app_id)
    old_image = old_app.provider_config.image if old_app else None
    record = config_store.record_from_app(app_update, state.installed_records[app_id])
    resolved = config_store.set_record(record)
    await config_store.save_installed_apps()
    if not resolved:
        raise HTTPException(status_code=422, detail="Application could not be resolved against its store.")
    if old_image != resolved.provider_config.image:
        asyncio.create_task(pull_and_cache_image(resolved.provider_config.image))
    return resolved


@router.patch("/apps/installed/{app_id}", response_model=InstalledApp)
async def patch_installed_app(
    app_id: str, decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)
) -> InstalledApp:
    """Partially update an installed app.

    Record fields (``users``, ``groups``, ``app_template``, ``auto_update``,
    ``home_directories``) are applied directly; any other ``InstalledApp``
    field is merged into the record's overrides.
    """
    record = state.installed_records.get(app_id)
    if not record:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    if not isinstance(decrypted_body, dict) or not decrypted_body:
        raise HTTPException(status_code=422, detail="Request body must be a non-empty object.")

    patch = dict(decrypted_body)
    if "logo" in patch:
        patch["logo"] = _save_logo(app_id, patch["logo"])
    if isinstance(patch.get("provider_config"), dict):
        _normalise_scripts(patch["provider_config"])

    old_app = state.installed_apps.get(app_id)
    old_image = old_app.provider_config.image if old_app else None
    updated = config_store.apply_partial_update(record, patch)
    resolved = config_store.set_record(updated)
    await config_store.save_installed_apps()
    if not resolved:
        raise HTTPException(status_code=422, detail="Application could not be resolved against its store.")
    if old_image != resolved.provider_config.image:
        asyncio.create_task(pull_and_cache_image(resolved.provider_config.image))
    return resolved


@router.delete("/apps/installed/{app_id}", status_code=204)
async def delete_installed_app(app_id: str) -> Response:
    """Uninstall an app (and purge a meta-app's icon and home template)."""
    record = state.installed_records.get(app_id)
    if not record:
        raise HTTPException(status_code=404, detail="Installed app not found.")

    if record.is_meta_app:
        icon_path = os.path.join(settings.app_icons_path, f"{app_id}.png")
        if os.path.exists(icon_path):
            try:
                os.remove(icon_path)
                logger.info("Deleted custom icon for meta-app %s", app_id)
            except OSError as exc:
                logger.error("Failed to delete icon for meta-app %s: %s", app_id, exc)
        if record.home_template_name:
            template_dir = os.path.join(settings.home_templates_path, record.home_template_name)
            if os.path.isdir(template_dir):
                try:
                    safe_rmtree(template_dir)
                    logger.info("Purged home template directory for meta-app %s", app_id)
                except (OSError, HTTPException) as exc:
                    logger.error("Failed to purge home template for meta-app %s: %s", app_id, exc)

    config_store.remove_record(app_id)
    await config_store.save_installed_apps()
    return Response(status_code=204)


@router.post("/apps/installed/{app_id}/check_update", response_model=ImageUpdateCheckResponse)
async def check_app_update(app_id: str) -> ImageUpdateCheckResponse:
    """Compare the local image digest with the registry."""
    app = state.installed_apps.get(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    image_name = app.provider_config.image
    provider = DockerProvider(app.model_dump())
    local_info = await provider.get_local_image_info(image_name)
    remote_digest = await provider.get_remote_image_digest(image_name)
    if not remote_digest:
        raise HTTPException(
            status_code=502,
            detail=f"Could not retrieve update information for {image_name} from its registry.",
        )
    local_digests = local_info.get("digests", []) if local_info else []
    return ImageUpdateCheckResponse(
        current_sha=local_info["short_id"] if local_info else None,
        update_available=not any(remote_digest in digest for digest in local_digests),
    )


@router.post("/apps/installed/{app_id}/pull_latest", response_model=ImagePullResponse)
async def pull_latest_app_image(app_id: str) -> ImagePullResponse:
    """Pull the latest image of an app and refresh its autostart cache."""
    app = state.installed_apps.get(app_id)
    if not app:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    image_name = app.provider_config.image
    try:
        await DockerProvider(app.model_dump()).pull_image(image_name)
        await config_store.refresh_autostart_for_app(app)
        await get_and_cache_image_metadata(image_name, force_refresh=True)
        return ImagePullResponse(
            status="success", new_sha=state.image_metadata.get(image_name, {}).get("sha")
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Error pulling image for app %s: %s", app_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to pull image: {exc}") from exc


@router.post("/apps/meta", response_model=InstalledApp, status_code=201)
async def create_meta_app(decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)) -> InstalledApp:
    """Create a meta-app: a copy of an installed app with its own home template."""
    try:
        req = CreateMetaAppRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    base_record = state.installed_records.get(req.base_app_id)
    base_app = state.installed_apps.get(req.base_app_id)
    if not base_record or not base_app:
        raise HTTPException(
            status_code=404, detail=f"Base application with ID '{req.base_app_id}' not found."
        )

    new_app_id = str(uuid.uuid4())
    home_template_name = f"meta_{new_app_id}"
    try:
        template_dir = os.path.join(settings.home_templates_path, home_template_name)
        os.makedirs(os.path.join(template_dir, "Desktop", "files"), exist_ok=True, mode=0o755)
        os.chmod(template_dir, 0o700)
        logo_url = _save_logo(new_app_id, req.logo) if req.logo else base_app.logo

        overrides = config_store.deep_merge(
            base_record.overrides,
            {
                "name": req.name,
                "logo": logo_url,
                "provider_config": {
                    "custom_autostart_script_b64": req.custom_autostart_script_b64,
                    "custom_autostart_wayland_script_b64": req.custom_autostart_wayland_script_b64,
                },
            },
        )
        record = InstalledAppRecord(
            id=new_app_id,
            source=base_record.source,
            source_app_id=base_record.source_app_id,
            app_template=base_record.app_template,
            users=req.users,
            groups=req.groups,
            auto_update=False,
            home_directories=base_record.home_directories,
            is_meta_app=True,
            base_app_id=req.base_app_id,
            home_template_name=home_template_name,
            overrides=overrides,
        )
        resolved = config_store.set_record(record)
        await config_store.save_installed_apps()
        if not resolved:
            raise HTTPException(status_code=422, detail="Meta app could not be resolved.")
        return resolved
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Error creating meta app: %s", exc)
        raise HTTPException(
            status_code=500, detail="Internal server error while creating meta app."
        ) from exc


@router.post("/launch/meta_customize", response_model=LaunchResponse)
async def launch_meta_for_customization(
    decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body),
    auth_user: dict[str, Any] = Depends(verify_admin),
) -> dict[str, str]:
    """Launch a meta-app with its home template mounted read-write."""
    try:
        req = LaunchMetaCustomizeRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    app = state.installed_apps.get(req.application_id)
    if not app or not app.is_meta_app or not app.home_template_name:
        raise HTTPException(
            status_code=400, detail="This endpoint is only for customizing meta applications."
        )
    template_path = os.path.join(settings.home_templates_path, app.home_template_name)
    return await launch_application(
        application_id=req.application_id,
        username=auth_user["username"],
        effective_settings=auth_user["effective_settings"],
        home_name=None,
        env_vars={},
        language=req.language,
        selected_gpu=req.selected_gpu,
        forced_rw_mount=template_path,
        wayland_mode=req.wayland_mode,
    )


# --- Templates -------------------------------------------------------------


@router.get("/apps/templates", response_model=list[AppTemplate])
async def get_app_templates() -> list[dict[str, Any]]:
    """List app templates."""
    return sorted(state.app_templates.values(), key=lambda t: t["name"])


@router.post("/apps/templates", response_model=AppTemplate, status_code=201)
async def save_app_template(decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)) -> AppTemplate:
    """Create or replace a template."""
    try:
        template = AppTemplate(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _require_safe_name(template.name, "template")
    try:
        await config_store.save_app_template(template)
    except OSError as exc:
        logger.error("Error saving app template: %s", exc)
        raise HTTPException(
            status_code=500, detail="Internal server error while saving template."
        ) from exc
    return template


@router.delete("/apps/templates/{template_name}", status_code=204)
async def delete_app_template(template_name: str) -> Response:
    """Delete a user template."""
    _require_safe_name(template_name, "template")
    try:
        config_store.delete_app_template(template_name)
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Cannot delete the default template '{template_name}'. You can override it by "
                "creating a new template with the same name."
            ),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found.") from exc
    except OSError as exc:
        logger.error("Error deleting template '%s': %s", template_name, exc)
        raise HTTPException(status_code=500, detail="Failed to delete template file.") from exc
    return Response(status_code=204)


# --- Sessions --------------------------------------------------------------


@router.get("/sessions", response_model=list[UserSessionList])
async def get_all_sessions() -> list[UserSessionList]:
    """List every session grouped by user."""
    by_user: dict[str, list] = defaultdict(list)
    for sid, data in state.sessions.items():
        by_user[data.get("username", "unknown")].append(session_info(sid, data, for_owner=False))
    response = [
        UserSessionList(username=name, sessions=sorted(items, key=lambda s: s.created_at, reverse=True))
        for name, items in by_user.items()
    ]
    return sorted(response, key=lambda u: u.username)


@router.delete("/sessions/{session_id}", status_code=204)
async def stop_any_session(session_id: str) -> Response:
    """Stop any user's session."""
    if session_id not in state.sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    await stop_session(session_id)
    return Response(status_code=204)


# --- Admins, users, groups -------------------------------------------------


@router.post("/admins", response_model=CreateUserResponse, status_code=201)
async def create_admin(decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Create an administrator."""
    try:
        req = CreateAdminRequest(**decrypted_body)
        user, private_key = user_manager.create_admin(req.username, req.public_key)
        return {"user": user, "private_key": private_key}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc


@router.delete("/admins/{username}", status_code=204)
async def delete_admin(username: str) -> Response:
    """Delete an administrator."""
    try:
        user_manager.delete_admin(username)
        return Response(status_code=204)
    except ValueError as exc:
        if "cannot be deleted" in str(exc).lower():
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users", response_model=CreateUserResponse, status_code=201)
async def create_user(decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Create a user."""
    try:
        req = CreateUserRequest(**decrypted_body)
        user, private_key = user_manager.create_user(
            req.username, req.public_key, req.settings.model_dump()
        )
        return {"user": user, "private_key": private_key}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc


@router.put("/users/{username}", response_model=User)
async def update_user(
    username: str, decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)
) -> dict[str, Any]:
    """Replace a user's settings."""
    try:
        req = UpdateUserRequest(**decrypted_body)
        user_manager.update_user_settings(username, req.settings.model_dump())
        return user_manager.get_user(username)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc


@router.delete("/users/{username}", status_code=204)
async def delete_user(username: str) -> Response:
    """Delete a user and their storage."""
    try:
        user_manager.delete_user(username)
        return Response(status_code=204)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_storage_user(username: str) -> None:
    """Ensure a user exists and has persistent storage enabled."""
    if not user_manager.get_user(username):
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    if not user_manager.get_effective_settings(username).get("persistent_storage", False):
        raise HTTPException(status_code=403, detail="Persistent storage is disabled for this user.")


def _require_admin_user(username: str) -> None:
    """Ensure ``username`` is an administrator."""
    user = user_manager.get_user(username)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=404, detail=f"Admin '{username}' not found.")


@router.get("/users/{username}/homedirs", response_model=HomeDirectoryList)
async def list_user_home_dirs(username: str) -> dict[str, list[str]]:
    """List a user's home directories."""
    _require_storage_user(username)
    return {"home_dirs": user_manager.get_home_dirs(username)}


@router.post("/users/{username}/homedirs", status_code=201)
async def create_user_home_dir(
    username: str, decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)
) -> dict[str, str]:
    """Create a home directory for a user."""
    _require_storage_user(username)
    try:
        req = HomeDirectoryCreate(**decrypted_body)
        user_manager.create_home_dir(username, req.home_name)
        return {"status": "success"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc


@router.delete("/users/{username}/homedirs/{home_name}", status_code=204)
async def delete_user_home_dir(username: str, home_name: str) -> Response:
    """Delete a user's home directory."""
    _require_storage_user(username)
    try:
        user_manager.delete_home_dir(username, home_name)
        return Response(status_code=204)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/admins/{username}/homedirs", response_model=HomeDirectoryList)
async def list_admin_home_dirs(username: str) -> dict[str, list[str]]:
    """List an administrator's home directories."""
    _require_admin_user(username)
    return {"home_dirs": user_manager.get_home_dirs(username)}


@router.post("/admins/{username}/homedirs", status_code=201)
async def create_admin_home_dir(
    username: str, decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)
) -> dict[str, str]:
    """Create a home directory for an administrator."""
    _require_admin_user(username)
    try:
        req = HomeDirectoryCreate(**decrypted_body)
        user_manager.create_home_dir(username, req.home_name)
        return {"status": "success"}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc


@router.delete("/admins/{username}/homedirs/{home_name}", status_code=204)
async def delete_admin_home_dir(username: str, home_name: str) -> Response:
    """Delete an administrator's home directory."""
    _require_admin_user(username)
    try:
        user_manager.delete_home_dir(username, home_name)
        return Response(status_code=204)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/groups", response_model=Group, status_code=201)
async def create_group(decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Create a group."""
    try:
        req = CreateGroupRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    if req.name in user_manager.GROUP_DATA:
        raise HTTPException(status_code=409, detail=f"Group '{req.name}' already exists.")
    user_manager.write_group_file(req.name, req.settings.model_dump())
    return user_manager.GROUP_DATA.get(req.name)


@router.put("/groups/{group_name}", response_model=Group)
async def update_group(
    group_name: str, decrypted_body: dict[str, Any] = Depends(get_decrypted_request_body)
) -> dict[str, Any]:
    """Replace a group's settings."""
    try:
        req = UpdateGroupRequest(**decrypted_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    if group_name not in user_manager.GROUP_DATA:
        raise HTTPException(status_code=404, detail=f"Group '{group_name}' not found.")
    user_manager.write_group_file(group_name, req.settings.model_dump())
    return user_manager.GROUP_DATA.get(group_name)


@router.delete("/groups/{group_name}", status_code=204)
async def delete_group(group_name: str) -> Response:
    """Delete a group."""
    try:
        user_manager.delete_group(group_name)
        return Response(status_code=204)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
