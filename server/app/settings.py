import os
import logging

SETTING_DEFINITIONS = [
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
        "default": "https://raw.githubusercontent.com/linuxserver/sealskin-apps/refs/heads/master/apps.yml",
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
        "help": "Directory for temporary file uploads.",
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
]


class AppSettings:
    """
    Parses and stores application settings from environment variables,
    with fallback to default values.
    """

    def __init__(self):
        self._process_and_set_attributes()

    def _process_and_set_attributes(self):
        """Process definitions and set them as class attributes."""
        for setting in SETTING_DEFINITIONS:
            name = setting["name"]
            stype = setting["type"]
            env_var_name = f"SEALSKIN_{name.upper()}"

            default_val = setting.get("default")
            raw_value = os.environ.get(env_var_name)

            if raw_value is None:
                processed_value = default_val
            else:
                try:
                    if stype == "bool":
                        processed_value = str(raw_value).lower() in ["true", "1", "yes"]
                    elif stype == "int":
                        processed_value = int(raw_value)
                    else:
                        processed_value = str(raw_value)
                except (ValueError, TypeError) as e:
                    logging.error(
                        f"Could not parse setting '{name}' with value '{raw_value}'. Using default. Error: {e}"
                    )
                    processed_value = default_val

            setattr(self, name, processed_value)


settings = AppSettings()
