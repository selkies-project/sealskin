"""Application settings.

Every setting is defined once in :data:`SETTING_DEFINITIONS` and can be
overridden with an environment variable named ``SEALSKIN_<NAME>`` (upper case).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .version import repo_root

SETTING_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "log_level",
        "type": "str",
        "default": "INFO",
        "help": "Logging level (e.g., DEBUG, INFO, WARNING).",
    },
    {
        "name": "api_port",
        "type": "int",
        "default": 8000,
        "help": "Port for the main API server.",
    },
    {
        "name": "session_port",
        "type": "int",
        "default": 8443,
        "help": "Port for the session proxy server.",
    },
    {
        "name": "default_provider",
        "type": "str",
        "default": "docker",
        "help": "The default application provider to use.",
    },
    {
        "name": "app_resource_path",
        "type": "str",
        "default": (
            "https://raw.githubusercontent.com/linuxserver/sealskin-apps/refs/heads/master/apps.yml"
        ),
        "help": "URL for the YAML file defining default available applications.",
    },
    {
        "name": "installed_apps_path",
        "type": "str",
        "default": "/config/.config/sealskin/installed_apps.yml",
        "help": "Path to the YAML file for installed application configurations.",
    },
    {
        "name": "app_stores_path",
        "type": "str",
        "default": "/config/.config/sealskin/app_stores.yml",
        "help": "Path to the YAML file defining available app stores.",
    },
    {
        "name": "app_templates_path",
        "type": "str",
        "default": "/config/.config/sealskin/app_templates",
        "help": "Path to the directory for user-defined application templates.",
    },
    {
        "name": "default_app_templates_path",
        "type": "str",
        "default": "app/default_templates",
        "help": "Path to the directory for default application templates.",
    },
    {
        "name": "upload_dir",
        "type": "str",
        "default": "/storage/sealskin_uploads",
        "help": "Directory for temporary file uploads (one sub-directory per user).",
    },
    {
        "name": "session_cookie_name",
        "type": "str",
        "default": "sealskin_session_token",
        "help": "Name of the session cookie.",
    },
    {
        "name": "autostart_cache_path",
        "type": "str",
        "default": "/config/.config/sealskin/autostart_cache",
        "help": "Path to cache autostart scripts.",
    },
    {
        "name": "app_store_cache_path",
        "type": "str",
        "default": "/config/.config/sealskin/app_stores_cache",
        "help": "Path to cache app store YAML files.",
    },
    {
        "name": "auto_update_apps",
        "type": "bool",
        "default": True,
        "help": "Enable automatic pulling of the latest app images in the background.",
    },
    {
        "name": "auto_update_interval_seconds",
        "type": "int",
        "default": 3600,
        "help": "How often to check for app image updates (in seconds).",
    },
    {
        "name": "puid",
        "type": "int",
        "default": 1000,
        "help": "Default User ID to run containers as.",
    },
    {
        "name": "pgid",
        "type": "int",
        "default": 1000,
        "help": "Default Group ID to run containers as.",
    },
    {
        "name": "keys_base_path",
        "type": "str",
        "default": "/config/.config/sealskin/keys",
        "help": "Base directory for admin and user public keys.",
    },
    {
        "name": "groups_base_path",
        "type": "str",
        "default": "/config/.config/sealskin/groups",
        "help": "Base directory for group definition files.",
    },
    {
        "name": "storage_path",
        "type": "str",
        "default": "/storage",
        "help": "Base directory for user home directories.",
    },
    {
        "name": "app_icons_path",
        "type": "str",
        "default": "/storage/sealskin_app_icons",
        "help": "Directory for storing custom-uploaded application icons.",
    },
    {
        "name": "home_templates_path",
        "type": "str",
        "default": "/storage/sealskin_home_templates",
        "help": "Base directory for meta-app home directory templates.",
    },
    {
        "name": "container_config_path",
        "type": "str",
        "default": "/config",
        "help": "Mount point for home directories inside the container.",
    },
    {
        "name": "server_private_key_path",
        "type": "str",
        "default": "/config/ssl/server_key.pem",
        "help": "Path to the server private key PEM file.",
    },
    {
        "name": "proxy_key_path",
        "type": "str",
        "default": "/config/ssl/proxy_key.pem",
        "help": "Path to the proxy SSL private key file.",
    },
    {
        "name": "proxy_cert_path",
        "type": "str",
        "default": "/config/ssl/proxy_cert.pem",
        "help": "Path to the proxy SSL certificate file.",
    },
    {
        "name": "public_storage_path",
        "type": "str",
        "default": "/storage/sealskin_public",
        "help": "Directory for storing publicly shared files.",
    },
    {
        "name": "public_shares_metadata_path",
        "type": "str",
        "default": "/config/.config/sealskin/public_shares.yml",
        "help": "Path to the YAML file for public share metadata.",
    },
    {
        "name": "share_cleanup_interval_seconds",
        "type": "int",
        "default": 600,
        "help": "How often to run the cleanup job for expired shares (in seconds).",
    },
    {
        "name": "sessions_db_path",
        "type": "str",
        "default": "/config/.config/sealskin/sessions.yml",
        "help": "Path to the YAML file for session persistence.",
    },
    {
        "name": "caddyfile_path",
        "type": "str",
        "default": "/config/.config/sealskin/Caddyfile",
        "help": "Path to the generated Caddyfile for the proxy.",
    },
    {
        "name": "ui_path",
        "type": "str",
        "default": os.path.join(repo_root(), "client", "dist", "ui"),
        "help": "Directory holding the built web UI served under /ui.",
    },
    {
        "name": "template_schema_path",
        "type": "str",
        "default": os.path.join(os.path.dirname(os.path.abspath(__file__)), "template_schema.yml"),
        "help": "YAML file describing the environment variables editable in app templates.",
    },
    {
        "name": "crypto_session_ttl_seconds",
        "type": "int",
        "default": 86400,
        "help": "Idle lifetime of an E2EE session key before it is discarded.",
    },
    {
        "name": "watch_config_files",
        "type": "bool",
        "default": True,
        "help": "Reload YAML configuration files automatically when they change on disk.",
    },
]


class AppSettings:
    """Settings parsed from environment variables with fallback to defaults.

    Each entry of :data:`SETTING_DEFINITIONS` becomes an attribute of the
    instance (for example ``settings.api_port``).
    """

    def __init__(self) -> None:
        """Populate attributes from the environment."""
        self._process_and_set_attributes()

    def _process_and_set_attributes(self) -> None:
        """Parse every definition and set it as an attribute."""
        for setting in SETTING_DEFINITIONS:
            name = setting["name"]
            stype = setting["type"]
            env_var_name = f"SEALSKIN_{name.upper()}"

            default_val = setting.get("default")
            raw_value = os.environ.get(env_var_name)

            if raw_value is None:
                processed_value: Any = default_val
            else:
                try:
                    if stype == "bool":
                        processed_value = str(raw_value).lower() in ["true", "1", "yes"]
                    elif stype == "int":
                        processed_value = int(raw_value)
                    else:
                        processed_value = str(raw_value)
                except (ValueError, TypeError) as exc:
                    logging.error(
                        "Could not parse setting '%s' with value '%s'. Using default. Error: %s",
                        name,
                        raw_value,
                        exc,
                    )
                    processed_value = default_val

            setattr(self, name, processed_value)


settings = AppSettings()
