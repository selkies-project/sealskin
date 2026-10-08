"""Application settings.

Every setting is defined once in `SETTING_DEFINITIONS` and can be
overridden with an environment variable named `SEALSKIN_<NAME>` (upper case).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .version import repo_root

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_ui_path() -> str:
    """Return the default UI directory.

    Resolution order:
    1. `app/ui/` inside the installed package (wheel layout).
    2. `<repo>/client/dist/ui` (source / Docker layout).
    """
    pkg_ui = os.path.join(_PKG_DIR, "ui")
    if os.path.isdir(pkg_ui):
        return pkg_ui
    return os.path.join(repo_root(), "client", "dist", "ui")


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
        "default": "auto",
        "help": (
            "Backend that runs sessions: `docker`, `kubernetes`, or `auto`, which picks "
            "Kubernetes when the server runs in a Kubernetes pod and Docker otherwise."
        ),
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
        "name": "proot_catalogs_path",
        "type": "str",
        "default": "/config/.config/sealskin/proot_catalogs.yml",
        "help": "Path to the YAML file of the PRoot Apps catalogs, shared by the nodes of a cluster.",
    },
    {
        "name": "proot_apps_path",
        "type": "str",
        "default": "/storage/sealskin_proot_apps",
        "help": (
            "Directory this node keeps the content of every PRoot Apps catalog in, one folder "
            "per catalog, mounted read-only into the sessions of the users assigned to it."
        ),
    },
    {
        "name": "proot_apps_remote",
        "type": "str",
        "default": "linuxserver/proot-apps",
        "help": (
            "GitHub `owner/repo` of the PRoot Apps repository the catalog editor opens with; a "
            "fork lists its apps in `metadata/metadata.yml` and publishes them as tags of "
            "`ghcr.io/<owner>/<repo>`."
        ),
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
        "name": "branding_path",
        "type": "str",
        "default": "/config/.config/sealskin/branding",
        "help": (
            "Directory of the brand the web app wears: `branding.yml` and the logo and wallpaper "
            "an administrator uploads. Shared by every node of a cluster."
        ),
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
        "default": _default_ui_path(),
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
        "name": "sso_keys_path",
        "type": "str",
        "default": "/config/.config/sealskin/sso_keys.yml",
        "help": "Path to the YAML file of browser keys registered by signing in through an identity provider.",
    },
    {
        "name": "oidc_issuer",
        "type": "str",
        "default": "",
        "help": "Issuer URL of an OpenID Connect provider the web app offers sign-in with; empty offers none.",
    },
    {
        "name": "oidc_client_id",
        "type": "str",
        "default": "",
        "help": "Client ID of SealSkin at the OpenID Connect provider.",
    },
    {
        "name": "oidc_client_secret",
        "type": "str",
        "default": "",
        "help": "Client secret of SealSkin at the OpenID Connect provider; empty for a public client.",
    },
    {
        "name": "oidc_scopes",
        "type": "str",
        "default": "openid profile email",
        "help": "Scopes requested from the OpenID Connect provider.",
    },
    {
        "name": "saml_metadata_url",
        "type": "str",
        "default": "",
        "help": "Metadata URL of a SAML identity provider the web app offers sign-in with; empty offers none.",
    },
    {
        "name": "sso_username_claim",
        "type": "str",
        "default": "",
        "help": (
            "Claim (OpenID Connect) or attribute (SAML, by its `Name` or `FriendlyName`) naming the "
            "SealSkin user; empty takes `preferred_username`, or the SAML attribute `username`, else "
            "the SAML NameID."
        ),
    },
    {
        "name": "sso_groups_claim",
        "type": "str",
        "default": "groups",
        "help": "Claim or attribute listing the user's groups at the identity provider.",
    },
    {
        "name": "saml_username_attribute",
        "type": "str",
        "default": "",
        "help": (
            "SAML attribute naming the SealSkin user, by its `Name` or `FriendlyName`, where OpenID "
            "Connect is set up beside SAML and names its claim otherwise; empty takes `sso_username_claim`."
        ),
    },
    {
        "name": "saml_groups_attribute",
        "type": "str",
        "default": "",
        "help": "SAML attribute listing the user's groups; empty takes `sso_groups_claim`.",
    },
    {
        "name": "sso_admin_group",
        "type": "str",
        "default": "",
        "help": "Identity provider group whose members sign in as administrators; empty makes none.",
    },
    {
        "name": "sso_max_age_seconds",
        "type": "int",
        "default": 43200,
        "help": "Longest an identity provider sign-in lasts; one the provider ends sooner ends then.",
    },
    {
        "name": "store_url",
        "type": "str",
        "default": "file",
        "help": (
            "Where the objects the nodes of a cluster share are kept: `file` for this server's own "
            "files, or `s3://<bucket>/<prefix>?endpoint=<url>&region=<region>` for a bucket of an "
            "S3-compatible service."
        ),
    },
    {
        "name": "store_access_key",
        "type": "str",
        "default": "",
        "help": "Access key id for an S3 store.",
    },
    {
        "name": "store_secret_key",
        "type": "str",
        "default": "",
        "help": "Secret access key for an S3 store.",
    },
    {
        "name": "store_poll_seconds",
        "type": "int",
        "default": 15,
        "help": "How often a node looks for objects another node changed in a remote store.",
    },
    {
        "name": "cluster_path",
        "type": "str",
        "default": "/config/.config/sealskin/cluster",
        "help": "Directory of the cluster's shared records (nodes, pools, sign-in settings, home locations).",
    },
    {
        "name": "node_state_path",
        "type": "str",
        "default": "/config/.config/sealskin/node",
        "help": "Directory of this node's own state: web sign-ins, its join record, and usage not yet reported.",
    },
    {
        "name": "node_name",
        "type": "str",
        "default": "",
        "help": "Name this node shows in the dashboard; empty takes the host name.",
    },
    {
        "name": "node_roles",
        "type": "str",
        "default": "frontend,runtime",
        "help": (
            "Comma-separated roles of this node: `frontend` signs users in and proxies their "
            "sessions, `runtime` runs sessions."
        ),
    },
    {
        "name": "node_pool",
        "type": "str",
        "default": "default",
        "help": "Pool this node joins when it first registers; an administrator can move it later.",
    },
    {
        "name": "node_address",
        "type": "str",
        "default": "",
        "help": (
            "`host:port` other nodes reach this node's peer listener on; empty takes the host of "
            "`HOST_URL` and `peer_port`."
        ),
    },
    {
        "name": "node_max_sessions",
        "type": "int",
        "default": 0,
        "help": "Most sessions this node runs at once; 0 sets no limit.",
    },
    {
        "name": "node_gpu_slots",
        "type": "int",
        "default": 0,
        "help": "Most GPU sessions this node runs at once; 0 sets no limit.",
    },
    {
        "name": "peer_port",
        "type": "int",
        "default": 8444,
        "help": "Port of the listener other nodes of the cluster reach this one on.",
    },
    {
        "name": "peer_poll_seconds",
        "type": "int",
        "default": 10,
        "help": "How often a node asks every other node for its sessions and load.",
    },
    {
        "name": "join_url",
        "type": "str",
        "default": "",
        "help": (
            "`host:port` of the peer listener of a node of the cluster to join; with `join_code`, "
            "the node asks to join at start and keeps the cluster's records on that node."
        ),
    },
    {
        "name": "join_code",
        "type": "str",
        "default": "",
        "help": "Join code an administrator of the cluster issued for this node.",
    },
    {
        "name": "legacy_auth",
        "type": "bool",
        "default": True,
        "help": (
            "Accept the key-file clients (browser extension and mobile app configuration files) and "
            "their encrypted API; they reach this node alone, never the rest of a cluster."
        ),
    },
    {
        "name": "public_url",
        "type": "str",
        "default": "",
        "help": (
            "URL browsers reach the web app on, `https://sealskin.example.com`; empty takes "
            "`HOST_URL` and the session port. Set it behind a reverse proxy."
        ),
    },
    {
        "name": "session_domain",
        "type": "str",
        "default": "",
        "help": (
            "Domain whose every subdomain reaches this node, `apps.example.com`: with "
            "`session_isolation`, a session of a signed-in web user opens on `<session id>.<domain>`, "
            "apart from the web app. Needs wildcard DNS and a certificate for `*.<domain>`; empty "
            "takes the host of `public_url`."
        ),
    },
    {
        "name": "session_isolation",
        "type": "bool",
        "default": False,
        "help": (
            "Serve each session of a signed-in web user on an origin of its own, "
            "`<session id>.<session_domain>`, apart from the web app and the other sessions, "
            "which needs wildcard DNS and a certificate for every such name. Off serves sessions "
            "at `/<session id>/` on the web app's origin, where this node serves the "
            "application's web client itself and the container answers its API alone."
        ),
    },
    {
        "name": "web_client_path",
        "type": "str",
        "default": "/usr/share/selkies",
        "help": (
            "Directory of an application image holding the Selkies web client, one dashboard per "
            "subdirectory as the linuxserver images keep them, which this node exports once per "
            "image and serves to browsers in place of the container's own copy."
        ),
    },
    {
        "name": "trusted_proxies",
        "type": "str",
        "default": "",
        "help": (
            "Comma-separated addresses or networks of the reverse proxies in front of this node, "
            "whose forwarded client address and sign-in headers are believed."
        ),
    },
    {
        "name": "proxy_auth_user_header",
        "type": "str",
        "default": "",
        "help": (
            "Header a trusted proxy names the signed-in user in, `Remote-User`; empty takes no "
            "sign-in from a proxy."
        ),
    },
    {
        "name": "proxy_auth_groups_header",
        "type": "str",
        "default": "",
        "help": (
            "Header a trusted proxy lists the user's groups in, `Remote-Groups`, separated by commas or "
            "by `|`; empty takes no groups from a proxy. Name only a header the proxy sets."
        ),
    },
    {
        "name": "proxy_auth_unchecked",
        "type": "bool",
        "default": False,
        "help": (
            "Believe the proxy's sign-in header although this node cannot check, by reaching its own "
            "`public_url` or a trusted proxy's address, that the proxy removes the header a visitor "
            "sends. Leave it off wherever the check can run."
        ),
    },
    {
        "name": "proxy_auth_logout_url",
        "type": "str",
        "default": "",
        "help": (
            "URL the web app opens to sign out a user a proxy signed in, as the proxy's or its identity "
            "provider's logout page; empty leaves signing out to the proxy."
        ),
    },
    {
        "name": "http_port",
        "type": "int",
        "default": 0,
        "help": (
            "Port of a plain HTTP listener for a reverse proxy that terminates TLS, serving what the "
            "session port does; 0 opens none. It answers the addresses in `trusted_proxies` alone, and "
            "only requests they say arrived over HTTPS."
        ),
    },
    {
        "name": "root_token",
        "type": "str",
        "default": "",
        "help": (
            "Token the `root` administrator signs in to the web app with; empty generates one at "
            "first start and writes it to `root_token_path`."
        ),
    },
    {
        "name": "root_token_path",
        "type": "str",
        "default": "/config/root_token",
        "help": "File the generated root token is written to, for the administrator to copy and delete.",
    },
    {
        "name": "web_session_seconds",
        "type": "int",
        "default": 43200,
        "help": "Longest a web sign-in with the root token lasts unused.",
    },
    {
        "name": "root_sign_in",
        "type": "bool",
        "default": True,
        "help": (
            "Accept the root token on this node's web sign-in. Turn it off once administrators sign in "
            "through an identity provider; a node restarted with it on takes the token again."
        ),
    },
    {
        "name": "auto_sign_in",
        "type": "str",
        "default": "",
        "help": (
            "Identity provider, `oidc` or `saml`, the web app sends a signed-out browser to at once "
            "instead of showing its sign-in page; `#root` on the web app's address shows the page anyway."
        ),
    },
    {
        "name": "sso_force_login",
        "type": "bool",
        "default": False,
        "help": "Make the identity provider ask for credentials at every sign-in instead of reusing its session.",
    },
    {
        "name": "sso_create_users",
        "type": "bool",
        "default": True,
        "help": "Create a user at the first sign-in an identity provider or a proxy vouches for.",
    },
    {
        "name": "sso_hold_new_users",
        "type": "bool",
        "default": True,
        "help": (
            "Hold a user a sign-in creates, who may do nothing here, until an administrator places them in "
            "a group or approves them; a user the provider puts in a group is never held."
        ),
    },
    {
        "name": "usage_flush_seconds",
        "type": "int",
        "default": 300,
        "help": "How often a node adds the session time it ran to the cluster's usage records.",
    },
    {
        "name": "files_sync",
        "type": "str",
        "default": "auto",
        "help": (
            "Keep every user's shared files in the object store so each node a session starts on "
            "has them: `on`, `off`, or `auto`, which syncs once the cluster has a second node."
        ),
    },
    {
        "name": "shared_files_path",
        "type": "str",
        "default": "/storage/sealskin_shared_store",
        "help": "Directory a `file` store keeps the users' shared files in.",
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

    Each entry of `SETTING_DEFINITIONS` becomes an attribute of the
    instance (for example `settings.api_port`).
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


    def apply_overrides(self, values: dict[str, Any]) -> None:
        """Lay the cluster's written settings over the environment's.

        Only the settings in `CLUSTER_SETTINGS` are taken, each coerced to its
        declared type; one the cluster no longer sets goes back to what the
        environment or the default gave it.

        Args:
            values: The content of `cluster/settings.yml`.
        """
        held: dict[str, Any] = self.__dict__.setdefault("_overridden", {})
        types = {s["name"]: s["type"] for s in SETTING_DEFINITIONS}
        for name in CLUSTER_SETTINGS:
            value = values.get(name)
            if value is None or value == "":
                if name in held:
                    setattr(self, name, held.pop(name))
                continue
            held.setdefault(name, getattr(self, name))
            if types[name] == "bool":
                value = value if isinstance(value, bool) else str(value).lower() in ["true", "1", "yes"]
            elif types[name] == "int":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = held[name]
            else:
                value = str(value)
            setattr(self, name, value)


#: Settings an administrator may also write for the whole cluster, in `cluster/settings.yml`
#: of the shared store, where they take precedence over each node's environment.
CLUSTER_SETTINGS = (
    "oidc_issuer",
    "oidc_client_id",
    "oidc_client_secret",
    "oidc_scopes",
    "saml_metadata_url",
    "saml_username_attribute",
    "saml_groups_attribute",
    "sso_username_claim",
    "sso_groups_claim",
    "sso_admin_group",
    "sso_max_age_seconds",
    "sso_force_login",
    "sso_create_users",
    "sso_hold_new_users",
    "proxy_auth_user_header",
    "proxy_auth_groups_header",
    "proxy_auth_logout_url",
    "web_session_seconds",
    "auto_sign_in",
    "files_sync",
)

settings = AppSettings()
