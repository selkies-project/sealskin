"""Loading, saving and resolving the YAML configuration.

Installed applications are stored as *references plus overrides*: the record
names the store and the store app id, and ``overrides`` holds only the fields
the administrator changed. :func:`resolve_app` merges the processed store entry
with those overrides to produce the :class:`~app.models.InstalledApp` the rest
of the server works with. Store updates therefore apply automatically after
every cache refresh, and the file stays small enough to edit by hand.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import os
import re
from typing import Any

import httpx
import yaml
from pydantic import ValidationError

from . import persistence
from .models import (
    RECORD_FIELDS,
    AppStore,
    AppTemplate,
    InstalledApp,
    InstalledAppRecord,
    PublicShareMetadata,
)
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9 _.-]+$")
STORE_ENTRY_FIELDS: tuple[str, ...] = ("name", "logo", "url", "provider", "provider_config")


def is_safe_name(name: str) -> bool:
    """Tell whether a store or template name is safe to use in a file path.

    Args:
        name: Name supplied by an administrator.

    Returns:
        ``True`` when the name only contains letters, digits, spaces, ``_``,
        ``.`` and ``-`` and does not reduce to ``.`` or ``..``.
    """
    return bool(name) and bool(SAFE_NAME_RE.match(name)) and name.strip(". ") != ""


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overrides`` into a copy of ``base``.

    Dictionaries are merged key by key; lists and scalars in ``overrides``
    replace the base value.

    Args:
        base: Base dictionary (not modified).
        overrides: Values that win.

    Returns:
        A new merged dictionary.
    """
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def diff_overrides(full: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Return the subset of ``full`` that differs from ``base``.

    Nested dictionaries are compared recursively so an override only records
    the leaves that changed.

    Args:
        full: Complete desired values.
        base: Reference values (the store entry).

    Returns:
        A dictionary suitable for ``InstalledAppRecord.overrides``.
    """
    result: dict[str, Any] = {}
    for key, value in full.items():
        if key not in base:
            if value is not None and value != [] and value != {}:
                result[key] = copy.deepcopy(value)
            continue
        base_value = base[key]
        if isinstance(value, dict) and isinstance(base_value, dict):
            nested = diff_overrides(value, base_value)
            if nested:
                result[key] = nested
        elif value != base_value:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# App stores and their caches
# ---------------------------------------------------------------------------


def ensure_config_dir() -> None:
    """Create the configuration directories if they do not exist."""
    os.makedirs(os.path.dirname(settings.installed_apps_path), exist_ok=True)
    os.makedirs(settings.app_templates_path, exist_ok=True)
    os.makedirs(settings.app_store_cache_path, exist_ok=True)
    os.makedirs(settings.autostart_cache_path, exist_ok=True)


def load_app_stores() -> None:
    """Load ``app_stores.yml`` into ``state.app_stores``.

    A default store pointing at the LinuxServer catalogue is written when the
    file does not exist.
    """
    ensure_config_dir()
    try:
        stores_data = persistence.read_yaml(settings.app_stores_path)
        if stores_data is None:
            state.app_stores = [AppStore(name="SealSkin Apps", url=settings.app_resource_path)]
            save_app_stores_sync()
        else:
            stores: list[AppStore] = []
            for raw in stores_data or []:
                try:
                    stores.append(AppStore(**raw))
                except (ValidationError, TypeError) as exc:
                    logger.error("Skipping invalid app store entry %r: %s", raw, exc)
            state.app_stores = stores
        logger.info("Loaded %d app store(s).", len(state.app_stores))
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Error loading app stores config: %s", exc)
        state.app_stores = []


def save_app_stores_sync() -> None:
    """Write ``state.app_stores`` to disk synchronously."""
    ensure_config_dir()
    try:
        persistence.write_yaml_sync(
            settings.app_stores_path, [store.model_dump() for store in state.app_stores]
        )
    except OSError as exc:
        logger.error("Failed to save app stores config: %s", exc)


async def save_app_stores() -> None:
    """Write ``state.app_stores`` to disk."""
    ensure_config_dir()
    try:
        await persistence.write_yaml(
            settings.app_stores_path, [store.model_dump() for store in state.app_stores]
        )
    except OSError as exc:
        logger.error("Failed to save app stores config: %s", exc)


def get_store(store_name: str) -> AppStore | None:
    """Return the configured store with the given name, if any."""
    return next((s for s in state.app_stores if s.name == store_name), None)


def store_cache_file(store_name: str) -> str:
    """Return the cache file path of a store."""
    return os.path.join(settings.app_store_cache_path, f"{store_name}.yml")


def _extract_apps(data: Any) -> list[dict[str, Any]]:
    """Return the app list from a parsed store document.

    Raises:
        ValueError: If the document is neither a list nor ``{"apps": [...]}``.
    """
    if isinstance(data, dict) and "apps" in data:
        return list(data["apps"] or [])
    if isinstance(data, list):
        return data
    raise ValueError("App store YAML has an invalid format.")


def _read_cached_script_b64(store_name: str, app_id: str, suffix: str = "") -> str | None:
    """Return the cached autostart script of an app as base64, if present."""
    path = os.path.join(settings.autostart_cache_path, store_name, f"{app_id}{suffix}")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as handle:
                return base64.b64encode(handle.read()).decode("utf-8")
    except OSError as exc:
        logger.error("Failed to read autostart cache for %s%s: %s", app_id, suffix, exc)
    return None


def process_store_content(content: str, store_name: str) -> list[dict[str, Any]]:
    """Parse a store document into the entries the clients and resolver use.

    Cached autostart scripts are injected as base64 and nested extension lists
    are flattened, matching what the admin UI sends back at install time.

    Args:
        content: Raw YAML text of the store.
        store_name: Store name, used to locate the autostart cache.

    Returns:
        List of app dictionaries.

    Raises:
        yaml.YAMLError: If the document is not valid YAML.
        ValueError: If the document has an unexpected shape.
    """
    apps_list = _extract_apps(yaml.safe_load(content))
    processed: list[dict[str, Any]] = []
    for app in apps_list:
        if not isinstance(app, dict):
            continue
        app = copy.deepcopy(app)
        provider_config = app.setdefault("provider_config", {})
        if provider_config.get("autostart") and app.get("id"):
            provider_config["custom_autostart_script_b64"] = _read_cached_script_b64(
                store_name, app["id"]
            )
            provider_config["custom_autostart_wayland_script_b64"] = _read_cached_script_b64(
                store_name, app["id"], "-wayland"
            )
        extensions = provider_config.get("extensions")
        if extensions:
            flattened: list[str] = []
            for item in extensions:
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            provider_config["extensions"] = flattened
        processed.append(app)
    return processed


def load_store_entries() -> None:
    """Parse every cached store into ``state.store_entries``."""
    entries: dict[str, dict[str, dict[str, Any]]] = {}
    for store in state.app_stores:
        path = store_cache_file(store.name)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                apps = process_store_content(handle.read(), store.name)
            entries[store.name] = {app["id"]: app for app in apps if app.get("id")}
        except Exception as exc:  # noqa: BLE001
            logger.error("Error reading cached store '%s': %s", store.name, exc)
    state.store_entries = entries


def get_store_entry(store_name: str, app_id: str) -> dict[str, Any] | None:
    """Return the processed store entry for an app, if the store knows it."""
    return state.store_entries.get(store_name, {}).get(app_id)


async def _fetch_and_cache_app_store(store: AppStore) -> None:
    """Refresh one store's cache file using ETag conditional requests."""
    cache_path = store_cache_file(store.name)
    meta_path = cache_path + ".meta"
    headers: dict[str, str] = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
            if "etag" in meta:
                headers["If-None-Match"] = meta["etag"]
        except (json.JSONDecodeError, OSError):
            pass

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(store.url, timeout=15, headers=headers)
        if response.status_code == 304:
            logger.debug("App store '%s' is up to date.", store.name)
            return
        response.raise_for_status()
        try:
            yaml.safe_load(response.text)
        except yaml.YAMLError:
            logger.error("Invalid YAML content for store '%s'. Skipping cache.", store.name)
            return

        def write_cache_and_meta() -> None:
            with open(cache_path, "w", encoding="utf-8") as handle:
                handle.write(response.text)
            if "etag" in response.headers:
                with open(meta_path, "w", encoding="utf-8") as handle:
                    json.dump({"etag": response.headers["etag"]}, handle)

        await asyncio.to_thread(write_cache_and_meta)
        logger.info("Successfully cached app store '%s'.", store.name)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to update cache for app store '%s': %s", store.name, exc)


async def refresh_store_caches() -> None:
    """Refresh every configured store's cache and reload store entries."""
    logger.info("Starting app store cache refresh...")
    ensure_config_dir()
    await asyncio.gather(*(_fetch_and_cache_app_store(store) for store in state.app_stores))
    load_store_entries()
    logger.info("App store cache refresh complete.")


async def fetch_store_apps(store_name: str, url: str, refresh: bool) -> list[dict[str, Any]]:
    """Return the processed app list of a store, fetching it when needed.

    Args:
        store_name: Store name (validated by the caller).
        url: Store URL.
        refresh: Ignore the cache and fetch from ``url``.

    Returns:
        Processed app entries.

    Raises:
        httpx.RequestError: If the store cannot be fetched.
        ValueError, yaml.YAMLError: If the document is invalid.
    """
    cache_path = store_cache_file(store_name)
    if not refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as handle:
                return process_store_content(handle.read(), store_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cache read failed for %s: %s", store_name, exc)

    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True, timeout=15)
        response.raise_for_status()
    ensure_config_dir()
    with open(cache_path, "w", encoding="utf-8") as handle:
        handle.write(response.text)
    apps = process_store_content(response.text, store_name)
    load_store_entries()
    resolve_all_apps()
    return apps


# ---------------------------------------------------------------------------
# Autostart script cache
# ---------------------------------------------------------------------------


def autostart_cache_path(app: InstalledApp | InstalledAppRecord, suffix: str = "") -> str | None:
    """Return the cache file for an app's repository autostart script.

    Args:
        app: Installed app or record; ``source`` must name a configured store.
        suffix: ``""`` for X11 or ``"-wayland"``.

    Returns:
        The path, or ``None`` when the store is unknown.
    """
    store = get_store(app.source)
    if not store:
        return None
    return os.path.join(settings.autostart_cache_path, store.name, f"{app.source_app_id}{suffix}")


async def _fetch_and_cache_single_script(
    store_name: str, base_url: str, app_id: str, suffix: str = ""
) -> None:
    """Fetch one autostart script into the cache using ETag conditional requests."""
    cache_dir = os.path.join(settings.autostart_cache_path, store_name)
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{app_id}{suffix}")
    meta_path = cache_path + ".meta"
    headers: dict[str, str] = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
            if "etag" in meta:
                headers["If-None-Match"] = meta["etag"]
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read meta file for %s%s", app_id, suffix)

    autostart_url = f"{base_url}/autostart/{app_id}{suffix}"
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(autostart_url, timeout=10, headers=headers)
        if response.status_code == 304:
            logger.debug("Autostart script for '%s%s' in '%s' is up to date.", app_id, suffix, store_name)
            return
        if response.status_code == 404:
            with open(cache_path, "w", encoding="utf-8") as handle:
                handle.write("")
            if os.path.exists(meta_path):
                os.remove(meta_path)
            return
        response.raise_for_status()
        script_content = response.text

        def write_cache_and_meta() -> None:
            with open(cache_path, "w", encoding="utf-8") as handle:
                handle.write(script_content)
            if "etag" in response.headers:
                with open(meta_path, "w", encoding="utf-8") as handle:
                    json.dump({"etag": response.headers["etag"]}, handle)

        await asyncio.to_thread(write_cache_and_meta)
        logger.info("Cached autostart script for '%s%s' from store '%s'.", app_id, suffix, store_name)
    except httpx.RequestError as exc:
        logger.warning("Failed to fetch autostart script for '%s%s': %s", app_id, suffix, exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error updating autostart script for '%s%s': %s", app_id, suffix, exc)


async def refresh_autostart_caches() -> None:
    """Fetch autostart scripts for every store app that declares one."""
    logger.info("Starting autostart script cache refresh for all app stores...")
    tasks = []
    for store in state.app_stores:
        base_url = store.url.rsplit("/", 1)[0]
        apps = state.store_entries.get(store.name)
        if apps is None:
            path = store_cache_file(store.name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    apps = {a["id"]: a for a in _extract_apps(yaml.safe_load(handle)) if a.get("id")}
            except Exception as exc:  # noqa: BLE001
                logger.error("Error reading cached store '%s': %s", store.name, exc)
                continue
        for app_id, app in apps.items():
            if app.get("provider_config", {}).get("autostart"):
                tasks.append(_fetch_and_cache_single_script(store.name, base_url, app_id, ""))
                tasks.append(_fetch_and_cache_single_script(store.name, base_url, app_id, "-wayland"))
    if tasks:
        await asyncio.gather(*tasks)
    load_store_entries()
    logger.info("Autostart script cache refresh complete.")


async def refresh_autostart_for_app(app: InstalledApp) -> None:
    """Refresh the cached autostart scripts of a single installed app.

    Args:
        app: The resolved installed app.
    """
    if not app.provider_config.autostart:
        return
    store = get_store(app.source)
    if not store:
        logger.error(
            "Could not find app store named '%s' for app '%s'. Cannot fetch autostart script.",
            app.source,
            app.name,
        )
        return
    if not (store.url.endswith(".yml") or store.url.endswith(".yaml")):
        logger.error("App store URL does not appear to be a YAML file: %s", store.url)
        return
    base_url = store.url.rsplit("/", 1)[0]
    await _fetch_and_cache_single_script(store.name, base_url, app.source_app_id, "")
    await _fetch_and_cache_single_script(store.name, base_url, app.source_app_id, "-wayland")
    load_store_entries()
    resolve_all_apps()


# ---------------------------------------------------------------------------
# Installed apps: records, resolution, migration
# ---------------------------------------------------------------------------


def _store_base(record: InstalledAppRecord) -> dict[str, Any] | None:
    """Return the store entry restricted to the fields an installed app derives."""
    entry = get_store_entry(record.source, record.source_app_id)
    if not entry:
        return None
    return {key: copy.deepcopy(entry[key]) for key in STORE_ENTRY_FIELDS if key in entry}


def resolve_app(record: InstalledAppRecord) -> InstalledApp | None:
    """Compute the effective installed app from a record.

    Args:
        record: Reference plus overrides as stored on disk.

    Returns:
        The resolved :class:`InstalledApp`, or ``None`` (logged) when the
        store entry is missing and the overrides alone are incomplete.
    """
    base = _store_base(record) or {}
    merged = deep_merge(base, record.overrides)
    for key in RECORD_FIELDS:
        merged[key] = getattr(record, key)
    try:
        return InstalledApp(**merged)
    except ValidationError as exc:
        logger.error(
            "Installed app '%s' (%s/%s) cannot be resolved and is skipped: %s",
            record.id,
            record.source,
            record.source_app_id,
            exc.errors()[0].get("msg", exc) if exc.errors() else exc,
        )
        return None


def record_from_app(app: InstalledApp, existing: InstalledAppRecord | None = None) -> InstalledAppRecord:
    """Convert a full installed app into a reference-plus-overrides record.

    Args:
        app: Complete app as received from the admin UI.
        existing: Current record of the same app, whose overrides are replaced.

    Returns:
        The record to persist.
    """
    full = app.model_dump()
    record_fields = {key: full.pop(key) for key in RECORD_FIELDS}
    temp = InstalledAppRecord(**record_fields)
    base = _store_base(temp)
    overrides = diff_overrides(full, base) if base else full
    return InstalledAppRecord(**record_fields, overrides=overrides)


def apply_partial_update(record: InstalledAppRecord, patch: dict[str, Any]) -> InstalledAppRecord:
    """Merge a partial update into a record.

    Record fields (users, groups, template, ...) are set directly; everything
    else is deep-merged into ``overrides``.

    Args:
        record: Current record.
        patch: Partial ``InstalledApp`` shaped dictionary.

    Returns:
        The updated record (a new object).
    """
    data = record.model_dump()
    override_patch: dict[str, Any] = {}
    for key, value in patch.items():
        if key == "id":
            continue
        if key in RECORD_FIELDS:
            data[key] = value
        elif key == "overrides" and isinstance(value, dict):
            override_patch = deep_merge(override_patch, value)
        else:
            override_patch[key] = value
    data["overrides"] = deep_merge(data.get("overrides", {}), override_patch)
    updated = InstalledAppRecord(**data)
    base = _store_base(updated)
    if base:
        updated.overrides = diff_overrides(deep_merge(base, updated.overrides), base)
    return updated


def _migrate_legacy_record(raw: dict[str, Any]) -> InstalledAppRecord:
    """Convert a pre-0.2 full snapshot into a reference-plus-overrides record."""
    record_fields = {key: raw.get(key) for key in RECORD_FIELDS if key in raw}
    temp = InstalledAppRecord(**record_fields)
    full = {key: value for key, value in raw.items() if key not in RECORD_FIELDS}
    base = _store_base(temp)
    if base is None and temp.is_meta_app and temp.base_app_id:
        # Legacy meta-apps stored the base app *name* in ``source``; recover the store.
        base_raw = next(
            (r for r in state.installed_records.values() if r.id == temp.base_app_id), None
        )
        if base_raw:
            temp.source = base_raw.source
            temp.source_app_id = base_raw.source_app_id
            record_fields["source"] = temp.source
            record_fields["source_app_id"] = temp.source_app_id
            base = _store_base(temp)
    overrides = diff_overrides(full, base) if base else full
    return InstalledAppRecord(**record_fields, overrides=overrides)


def load_installed_apps() -> None:
    """Load ``installed_apps.yml`` into ``state.installed_records`` and resolve.

    Legacy full-snapshot records are migrated once and the file rewritten.
    Invalid records are skipped with a log line instead of clearing the list.
    """
    ensure_config_dir()
    try:
        apps_data = persistence.read_yaml(settings.installed_apps_path, default=[]) or []
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Error loading installed apps config: %s", exc)
        return

    records: dict[str, InstalledAppRecord] = {}
    legacy_seen = False
    # Two passes so meta-app migration can look up its base app's record.
    pending_legacy: list[dict[str, Any]] = []
    for raw in apps_data:
        if not isinstance(raw, dict):
            logger.error("Skipping malformed installed app entry: %r", raw)
            continue
        if "overrides" not in raw and "provider_config" in raw:
            pending_legacy.append(raw)
            continue
        try:
            record = InstalledAppRecord(**raw)
            records[record.id] = record
        except ValidationError as exc:
            logger.error("Skipping invalid installed app entry %r: %s", raw.get("id"), exc)
    state.installed_records = records
    for raw in sorted(pending_legacy, key=lambda r: bool(r.get("is_meta_app"))):
        legacy_seen = True
        try:
            record = _migrate_legacy_record(raw)
            state.installed_records[record.id] = record
        except ValidationError as exc:
            logger.error("Skipping invalid legacy installed app entry %r: %s", raw.get("id"), exc)

    resolve_all_apps()
    logger.info("Loaded %d installed application(s).", len(state.installed_apps))
    if legacy_seen:
        logger.info("Migrated installed apps to the reference+overrides format; rewriting file.")
        save_installed_apps_sync()


def resolve_all_apps() -> None:
    """Re-resolve every record into ``state.installed_apps``."""
    resolved: dict[str, InstalledApp] = {}
    for app_id, record in state.installed_records.items():
        app = resolve_app(record)
        if app:
            resolved[app_id] = app
    state.installed_apps = resolved


def _records_for_disk() -> list[dict[str, Any]]:
    """Serialise the records, dropping empty overrides for readability."""
    output = []
    for record in state.installed_records.values():
        data = record.model_dump()
        if not data.get("overrides"):
            data.pop("overrides", None)
        for key in ("base_app_id", "home_template_name"):
            if data.get(key) is None:
                data.pop(key, None)
        output.append(data)
    return output


def save_installed_apps_sync() -> None:
    """Write the installed app records synchronously."""
    ensure_config_dir()
    try:
        persistence.write_yaml_sync(settings.installed_apps_path, _records_for_disk())
    except OSError as exc:
        logger.error("Failed to save installed apps config: %s", exc)


async def save_installed_apps() -> None:
    """Write the installed app records to disk."""
    ensure_config_dir()
    try:
        await persistence.write_yaml(settings.installed_apps_path, _records_for_disk())
    except OSError as exc:
        logger.error("Failed to save installed apps config: %s", exc)


def set_record(record: InstalledAppRecord) -> InstalledApp | None:
    """Store a record in memory and resolve it.

    Args:
        record: The record to keep.

    Returns:
        The resolved app, or ``None`` if it could not be resolved (the record
        is still kept so the administrator can fix it).
    """
    state.installed_records[record.id] = record
    app = resolve_app(record)
    if app:
        state.installed_apps[record.id] = app
    else:
        state.installed_apps.pop(record.id, None)
    return app


def remove_record(app_id: str) -> None:
    """Forget an installed app."""
    state.installed_records.pop(app_id, None)
    state.installed_apps.pop(app_id, None)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _load_templates_from(directory: str, source: str) -> None:
    """Load every YAML template in ``directory`` into state."""
    if not os.path.isdir(directory):
        return
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith((".yml", ".yaml")):
            continue
        path = os.path.join(directory, filename)
        try:
            template_data = persistence.read_yaml(path, default={})
            name = template_data.get("name") if isinstance(template_data, dict) else None
            if name:
                template_data.setdefault("settings", {})
                state.app_templates[name] = {
                    "name": name,
                    "settings": template_data.get("settings") or {},
                }
                state.template_files[name] = path
        except Exception as exc:  # noqa: BLE001
            logger.error("Error loading %s template %s: %s", source, filename, exc)


def load_app_templates() -> None:
    """Load default and user templates into ``state.app_templates``.

    A blank ``Default`` template is created when none exist.
    """
    state.app_templates.clear()
    state.template_files.clear()
    _load_templates_from(settings.default_app_templates_path, "default")
    _load_templates_from(settings.app_templates_path, "user")

    if not state.app_templates:
        logger.warning("No app templates found. Creating a blank 'Default' template.")
        default_template = {"name": "Default", "settings": {}}
        path = os.path.join(settings.app_templates_path, "default.yml")
        try:
            persistence.write_yaml_sync(path, default_template)
        except OSError as exc:
            logger.error("Could not write default template file: %s", exc)
        state.app_templates["Default"] = default_template
        state.template_files["Default"] = path

    logger.info("Loaded %d application template(s).", len(state.app_templates))


def template_filename(name: str) -> str:
    """Return the file name derived from a template name."""
    return name.lower().replace(" ", "_") + ".yml"


async def save_app_template(template: AppTemplate) -> None:
    """Write a template to the user template directory and reload.

    When a template of that name was loaded from a differently named file
    (for example after a hand edit) the existing file is reused so no
    orphan is left behind.

    Args:
        template: Template to save.
    """
    existing = state.template_files.get(template.name)
    if existing and existing.startswith(os.path.abspath(settings.app_templates_path)):
        path = existing
    else:
        path = os.path.join(settings.app_templates_path, template_filename(template.name))
    await persistence.write_yaml(path, template.model_dump())
    load_app_templates()


def delete_app_template(name: str) -> None:
    """Delete a user template by name.

    Args:
        name: Template name.

    Raises:
        PermissionError: If the template is a default template.
        FileNotFoundError: If no template of that name exists.
    """
    path = state.template_files.get(name)
    user_dir = os.path.abspath(settings.app_templates_path)
    if path is None:
        candidate = os.path.join(settings.app_templates_path, template_filename(name))
        if os.path.exists(candidate):
            path = candidate
    if path is None:
        raise FileNotFoundError(name)
    if not os.path.abspath(path).startswith(user_dir + os.sep):
        raise PermissionError(name)
    os.remove(path)
    load_app_templates()


# ---------------------------------------------------------------------------
# Sessions and public shares
# ---------------------------------------------------------------------------


async def load_sessions() -> None:
    """Load persisted sessions into ``state.sessions``."""
    if not os.path.exists(settings.sessions_db_path):
        logger.info("Session persistence file not found. Starting with an empty session database.")
        return
    async with state.sessions_lock:
        try:
            loaded = persistence.read_yaml(settings.sessions_db_path, default={}) or {}
            if isinstance(loaded, dict):
                state.sessions.update(loaded)
            logger.info("Loaded %d session(s) from disk.", len(loaded))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load sessions from disk: %s", exc)


async def save_sessions() -> None:
    """Persist ``state.sessions`` to disk."""
    async with state.sessions_lock:
        snapshot = copy.deepcopy(state.sessions)
    try:
        await persistence.write_yaml(settings.sessions_db_path, snapshot)
        logger.debug("Saved %d session(s) to disk.", len(snapshot))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to save sessions to disk: %s", exc)


def load_public_shares() -> None:
    """Load public share metadata into ``state.public_shares``."""
    try:
        data = persistence.read_yaml(settings.public_shares_metadata_path, default={}) or {}
        shares: dict[str, PublicShareMetadata] = {}
        for share_id, metadata in data.items():
            try:
                shares[share_id] = PublicShareMetadata(**metadata)
            except (ValidationError, TypeError) as exc:
                logger.error("Skipping invalid public share %s: %s", share_id, exc)
        state.public_shares = shares
        logger.info("Loaded %d public share(s) from metadata file.", len(shares))
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Error loading public shares metadata: %s", exc)
        state.public_shares = {}


async def save_public_shares() -> None:
    """Persist ``state.public_shares`` to disk."""
    async with state.metadata_lock:
        snapshot = {
            share_id: metadata.model_dump(exclude_none=True)
            for share_id, metadata in state.public_shares.items()
        }
    try:
        await persistence.write_yaml(settings.public_shares_metadata_path, snapshot)
    except OSError as exc:
        logger.error("Failed to save public shares metadata: %s", exc)
