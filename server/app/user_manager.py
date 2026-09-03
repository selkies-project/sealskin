"""Users, administrators and groups stored as flat files.

* ``keys/admins/<username>``: the administrator's public key PEM.
* ``keys/users/<username>``: a ``--- Settings ---`` YAML block followed by a
  ``--- Public Key ---`` PEM block.
* ``groups/<name>``: YAML settings applied on top of member users' settings.

Files are re-scanned after every change (and by the configuration watcher
when they are edited by hand).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .settings import settings

logger = logging.getLogger(__name__)

USER_DATA: dict[str, dict[str, Any]] = {}
GROUP_DATA: dict[str, dict[str, Any]] = {}

DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "active": True,
    "group": "none",
    "persistent_storage": True,
    "public_sharing": False,
    "harden_container": False,
    "harden_openbox": False,
    "gpu": True,
    "storage_limit": -1,
    "session_limit": -1,
}

SERVER_PUBLIC_KEY_PEM: str | None = None
EXTERNAL_API_PORT: int = getattr(settings, "api_port", 8000)
EXTERNAL_SESSION_PORT: int = getattr(settings, "session_port", 8443)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def set_server_public_key(key: str) -> None:
    """Record the server public key used in generated admin config files."""
    global SERVER_PUBLIC_KEY_PEM
    SERVER_PUBLIC_KEY_PEM = key


def set_external_ports(api_port: int, session_port: int) -> None:
    """Record the externally reachable ports used in generated config files."""
    global EXTERNAL_API_PORT, EXTERNAL_SESSION_PORT
    EXTERNAL_API_PORT = api_port
    EXTERNAL_SESSION_PORT = session_port


def _atomic_write(path: str, content: str, mode: int = 0o600) -> None:
    """Write ``content`` to ``path`` atomically with the given permissions."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def _generate_key_pair(key_size: int = 2048) -> tuple[str, str]:
    """Generate an RSA key pair.

    Args:
        key_size: Modulus size in bits.

    Returns:
        ``(private_pem, public_pem)``.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def parse_key_file(path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a user file into settings and public key.

    Args:
        path: File under ``keys/users``.

    Returns:
        ``(settings, public_key_pem)``; both ``None`` when unreadable.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        parts = content.split("--- Public Key ---")
        settings_yaml = parts[0].replace("--- Settings ---", "").strip()
        pub_key_pem = parts[1].strip() if len(parts) > 1 else None
        user_settings = yaml.safe_load(settings_yaml) if settings_yaml else {}
        final_settings = DEFAULT_USER_SETTINGS.copy()
        if user_settings:
            final_settings.update(user_settings)
        return final_settings, pub_key_pem
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to parse user file %s: %s", path, exc)
        return None, None


def write_user_file(username: str, pub_key_pem: str, settings_dict: dict[str, Any]) -> None:
    """Write (or replace) a user's settings and public key file.

    Args:
        username: The user.
        pub_key_pem: Public key PEM.
        settings_dict: Settings to store.
    """
    file_path = os.path.join(settings.keys_base_path, "users", username)
    settings_yaml = yaml.safe_dump(settings_dict, default_flow_style=False, sort_keys=False)
    content = (
        "--- Settings ---\n"
        f"{settings_yaml.strip()}\n"
        "--- Public Key ---\n"
        f"{pub_key_pem.strip()}\n"
    )
    _atomic_write(file_path, content)
    logger.info("Wrote user file for '%s' at %s", username, file_path)


def _generate_default_admin() -> None:
    """Create the default ``admin`` account when no administrator exists.

    The generated private key is written to ``admin.json`` three levels above
    the keys directory (``/config/admin.json`` by default) for the operator to
    import into a client and then delete.
    """
    admin_dir = os.path.join(settings.keys_base_path, "admins")
    if os.path.exists(admin_dir) and os.listdir(admin_dir):
        return

    logger.warning("No admin users found. Creating a default 'admin' user.")
    os.makedirs(admin_dir, exist_ok=True)
    private_pem, public_pem = _generate_key_pair(4096)
    _atomic_write(os.path.join(admin_dir, "admin"), public_pem)

    config_path = os.path.abspath(os.path.join(settings.keys_base_path, "..", "..", "..", "admin.json"))
    admin_config = {
        "server_endpoint": os.environ.get("HOST_URL", "HOST_URL"),
        "api_port": EXTERNAL_API_PORT,
        "session_port": EXTERNAL_SESSION_PORT,
        "username": "admin",
        "private_key": private_pem,
        "server_public_key": SERVER_PUBLIC_KEY_PEM or "",
    }
    try:
        _atomic_write(config_path, json.dumps(admin_config, indent=2))
        logger.info("Generated default admin config at %s", config_path)
    except OSError as exc:
        logger.error("Failed to write admin config: %s", exc)


def load_users_and_groups() -> None:
    """Re-scan the key and group directories into memory."""
    logger.info("Reloading users, admins, and groups from filesystem...")
    USER_DATA.clear()
    GROUP_DATA.clear()

    admin_dir = os.path.join(settings.keys_base_path, "admins")
    user_dir = os.path.join(settings.keys_base_path, "users")
    os.makedirs(admin_dir, exist_ok=True)
    os.makedirs(user_dir, exist_ok=True)
    os.makedirs(settings.groups_base_path, exist_ok=True)

    _generate_default_admin()

    for username in sorted(os.listdir(admin_dir)):
        if username.startswith("."):
            continue
        try:
            with open(os.path.join(admin_dir, username), encoding="utf-8") as handle:
                pub_key = handle.read().strip()
            if pub_key:
                USER_DATA[username] = {"public_key": pub_key, "is_admin": True, "username": username}
        except OSError as exc:
            logger.error("Failed to load admin '%s': %s", username, exc)

    for username in sorted(os.listdir(user_dir)):
        if username.startswith(".") or username in USER_DATA:
            continue
        settings_dict, pub_key = parse_key_file(os.path.join(user_dir, username))
        if pub_key:
            USER_DATA[username] = {
                "public_key": pub_key,
                "settings": settings_dict,
                "is_admin": False,
                "username": username,
            }

    for group_name in sorted(os.listdir(settings.groups_base_path)):
        if group_name.startswith("."):
            continue
        try:
            with open(os.path.join(settings.groups_base_path, group_name), encoding="utf-8") as handle:
                group_settings = yaml.safe_load(handle)
            if group_settings:
                GROUP_DATA[group_name] = {"settings": group_settings, "name": group_name}
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load group %s: %s", group_name, exc)

    logger.info("Loaded %d user(s) and %d group(s).", len(USER_DATA), len(GROUP_DATA))


def get_user(username: str) -> dict[str, Any] | None:
    """Return a user record by name."""
    return USER_DATA.get(username)


def get_effective_settings(username: str) -> dict[str, Any]:
    """Return a user's settings with their group's settings applied on top.

    Administrators always get the defaults.
    """
    user = get_user(username)
    if not user or user.get("is_admin"):
        return DEFAULT_USER_SETTINGS.copy()
    base_settings = user.get("settings") or DEFAULT_USER_SETTINGS.copy()
    group_name = base_settings.get("group", "none")
    if group_name and group_name != "none" and group_name in GROUP_DATA:
        effective = base_settings.copy()
        effective.update(GROUP_DATA[group_name].get("settings", {}))
        return effective
    return base_settings


def get_all_users() -> list[dict[str, Any]]:
    """Return every non-admin user."""
    return [u for u in USER_DATA.values() if not u["is_admin"]]


def get_all_admins() -> list[dict[str, Any]]:
    """Return every administrator."""
    return [u for u in USER_DATA.values() if u["is_admin"]]


def get_all_groups() -> list[dict[str, Any]]:
    """Return every group."""
    return list(GROUP_DATA.values())


def _validate_username(username: str) -> None:
    """Raise ``ValueError`` for names that are not filesystem safe."""
    if not _NAME_RE.match(username or ""):
        raise ValueError("Invalid username. Use only letters, numbers, underscore, or hyphen.")


def create_admin(username: str, public_key: str | None) -> tuple[dict[str, Any], str | None]:
    """Create an administrator.

    Args:
        username: New admin name.
        public_key: Public key PEM, or ``None`` to generate a key pair.

    Returns:
        ``(user_record, private_key_pem_or_None)``.

    Raises:
        ValueError: For invalid or duplicate names.
    """
    _validate_username(username)
    if username in USER_DATA:
        raise ValueError(f"User or admin '{username}' already exists.")
    private_pem: str | None = None
    if public_key:
        public_pem = public_key
    else:
        private_pem, public_pem = _generate_key_pair()
    _atomic_write(os.path.join(settings.keys_base_path, "admins", username), public_pem.strip())
    load_users_and_groups()
    return get_user(username), private_pem


def delete_admin(username: str) -> None:
    """Delete an administrator and their storage.

    Raises:
        ValueError: For the root admin or unknown admins.
    """
    if username == "admin":
        raise ValueError("The root 'admin' account cannot be deleted.")
    user = get_user(username)
    if not user or not user.get("is_admin"):
        raise ValueError(f"Admin '{username}' not found.")

    user_storage_path = os.path.join(settings.storage_path, username)
    if os.path.isdir(user_storage_path):
        shutil.rmtree(user_storage_path)
        logger.info("Deleted storage for admin '%s'.", username)

    admin_file_path = os.path.join(settings.keys_base_path, "admins", username)
    if not os.path.exists(admin_file_path):
        raise ValueError(f"Admin file for '{username}' not found.")
    os.remove(admin_file_path)
    load_users_and_groups()
    logger.info("Deleted admin '%s'.", username)


def create_user(
    username: str, public_key: str | None, settings_dict: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Create a user.

    Args:
        username: New user name.
        public_key: Public key PEM, or ``None`` to generate a key pair.
        settings_dict: Initial settings.

    Returns:
        ``(user_record, private_key_pem_or_None)``.

    Raises:
        ValueError: For invalid or duplicate names.
    """
    _validate_username(username)
    if username in USER_DATA:
        raise ValueError(f"User '{username}' already exists.")
    private_pem: str | None = None
    if public_key:
        public_pem = public_key
    else:
        private_pem, public_pem = _generate_key_pair()
    write_user_file(username, public_pem, settings_dict)
    load_users_and_groups()
    return get_user(username), private_pem


def delete_user(username: str) -> None:
    """Delete a user and their storage.

    Raises:
        ValueError: For unknown users or administrators.
    """
    user = get_user(username)
    if not user:
        raise ValueError(f"User '{username}' not found.")
    if user.get("is_admin"):
        raise ValueError("Cannot delete an admin user.")

    user_storage_path = os.path.join(settings.storage_path, username)
    if os.path.isdir(user_storage_path):
        shutil.rmtree(user_storage_path)
        logger.info("Deleted storage for user '%s'.", username)

    file_path = os.path.join(settings.keys_base_path, "users", username)
    if not os.path.exists(file_path):
        raise ValueError(f"User file for '{username}' not found.")
    os.remove(file_path)
    load_users_and_groups()
    logger.info("Deleted user '%s'.", username)


def update_user_settings(username: str, new_settings: dict[str, Any]) -> None:
    """Replace a user's settings.

    Raises:
        ValueError: For unknown users or administrators.
    """
    user = get_user(username)
    if not user:
        raise ValueError(f"User '{username}' not found.")
    if user["is_admin"]:
        raise ValueError("Cannot update settings for an admin user.")
    write_user_file(username, user["public_key"], new_settings)
    load_users_and_groups()


def write_group_file(group_name: str, settings_dict: dict[str, Any]) -> None:
    """Create or replace a group file and reload.

    Args:
        group_name: Group name (validated by the API model).
        settings_dict: Settings applied to members.
    """
    file_path = os.path.join(settings.groups_base_path, group_name)
    _atomic_write(file_path, yaml.safe_dump(settings_dict, default_flow_style=False, sort_keys=False))
    logger.info("Wrote group file for '%s'.", group_name)
    load_users_and_groups()


def delete_group(group_name: str) -> None:
    """Delete a group.

    Raises:
        ValueError: For unknown groups.
    """
    if group_name not in GROUP_DATA:
        raise ValueError(f"Group '{group_name}' not found.")
    file_path = os.path.join(settings.groups_base_path, group_name)
    if not os.path.exists(file_path):
        raise ValueError(f"Group file for '{group_name}' not found.")
    os.remove(file_path)
    load_users_and_groups()
    logger.info("Deleted group '%s'.", group_name)


def get_home_dirs(username: str) -> list[str]:
    """List a user's home directories (sub-directories of their storage)."""
    user_storage_path = os.path.join(settings.storage_path, username)
    if not os.path.isdir(user_storage_path):
        return []
    try:
        return sorted(
            d for d in os.listdir(user_storage_path) if os.path.isdir(os.path.join(user_storage_path, d))
        )
    except OSError as exc:
        logger.error("Error listing home directories for %s: %s", username, exc)
        return []


def create_home_dir(username: str, home_name: str) -> None:
    """Create a home directory for a user.

    Raises:
        ValueError: For invalid names or existing directories.
        OSError: If the directory cannot be created.
    """
    if not _NAME_RE.match(home_name or ""):
        raise ValueError("Invalid home directory name. Use only letters, numbers, underscore, or hyphen.")
    new_home_path = os.path.join(settings.storage_path, username, home_name)
    if os.path.exists(new_home_path):
        raise ValueError(f"Home directory '{home_name}' already exists for user '{username}'.")
    try:
        os.makedirs(new_home_path, exist_ok=True, mode=0o755)
        os.makedirs(os.path.join(new_home_path, "Desktop", "files"), exist_ok=True, mode=0o755)
        logger.info("Created home directory '%s' for user '%s'.", home_name, username)
    except OSError as exc:
        logger.error("Failed to create home directory for %s: %s", username, exc)
        raise


def delete_home_dir(username: str, home_name: str) -> None:
    """Delete a user's home directory.

    Raises:
        ValueError: For invalid names or missing directories.
        OSError: If the directory cannot be removed.
    """
    if not _NAME_RE.match(home_name or ""):
        raise ValueError("Invalid home directory name.")
    home_path = os.path.join(settings.storage_path, username, home_name)
    if not os.path.isdir(home_path):
        raise ValueError(f"Home directory '{home_name}' not found for user '{username}'.")
    try:
        shutil.rmtree(home_path)
        logger.info("Deleted home directory '%s' for user '%s'.", home_name, username)
    except OSError as exc:
        logger.error("Failed to delete home directory for %s: %s", username, exc)
        raise
