import os
import logging
import yaml
import re
import shutil
from typing import Dict, Tuple, Optional, List
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .settings import settings

logger = logging.getLogger(__name__)

USER_DATA: Dict[str, Dict] = {}
GROUP_DATA: Dict[str, Dict] = {}

DEFAULT_USER_SETTINGS = {
    "active": True,
    "group": "none",
    "persistent_storage": True,
    "harden_container": False,
    "harden_openbox": False,
    "gpu": True,
    "storage_limit": -1,
    "session_limit": -1,
}


def parse_key_file(path: str) -> Tuple[Optional[Dict], Optional[str]]:
    try:
        with open(path, "r") as f:
            content = f.read()

        parts = content.split("--- Public Key ---")
        settings_yaml = parts[0].replace("--- Settings ---", "").strip()
        pub_key_pem = parts[1].strip() if len(parts) > 1 else None

        user_settings = yaml.safe_load(settings_yaml) if settings_yaml else {}

        final_settings = DEFAULT_USER_SETTINGS.copy()
        if user_settings:
            final_settings.update(user_settings)

        return final_settings, pub_key_pem
    except Exception as e:
        logger.error(f"Failed to parse user file {path}: {e}")
        return None, None


def write_user_file(username: str, pub_key_pem: str, settings_dict: dict):
    base_path = os.path.join(settings.keys_base_path, "users")
    os.makedirs(base_path, exist_ok=True)
    file_path = os.path.join(base_path, username)

    settings_yaml = yaml.dump(settings_dict, default_flow_style=False)

    content = (
        "--- Settings ---\n"
        f"{settings_yaml.strip()}\n"
        "--- Public Key ---\n"
        f"{pub_key_pem.strip()}\n"
    )

    with open(file_path, "w") as f:
        f.write(content)
    os.chmod(file_path, 0o600)
    logger.info(f"Wrote user file for '{username}' at {file_path}")


def _generate_default_admin():
    """Generates a default admin user if no admins exist."""
    admin_dir = os.path.join(settings.keys_base_path, "admins")
    admin_file_path = os.path.join(admin_dir, "admin")

    if os.path.exists(admin_dir) and os.listdir(admin_dir):
        return

    logger.warning("No admin users found. Creating a default 'admin' user.")
    os.makedirs(admin_dir, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    with open(admin_file_path, "w") as f:
        f.write(public_pem)
    os.chmod(admin_file_path, 0o600)

    logger.critical("\n" + "=" * 80)
    logger.critical("DEFAULT ADMIN CREDENTIALS (SAVE THIS PRIVATE KEY!)")
    logger.critical(f"Username: admin")
    logger.critical("Private Key:\n" + private_pem)
    logger.critical("=" * 80 + "\n")


def load_users_and_groups():
    """Scans key and group directories and populates in-memory dictionaries."""
    global USER_DATA, GROUP_DATA
    logger.info("Reloading users, admins, and groups from filesystem...")

    USER_DATA.clear()
    GROUP_DATA.clear()

    os.makedirs(os.path.join(settings.keys_base_path, "admins"), exist_ok=True)
    os.makedirs(os.path.join(settings.keys_base_path, "users"), exist_ok=True)
    os.makedirs(settings.groups_base_path, exist_ok=True)

    _generate_default_admin()

    admin_dir = os.path.join(settings.keys_base_path, "admins")
    for username in os.listdir(admin_dir):
        try:
            with open(os.path.join(admin_dir, username), "r") as f:
                pub_key = f.read().strip()
            if pub_key:
                USER_DATA[username] = {
                    "public_key": pub_key,
                    "is_admin": True,
                    "username": username,
                }
        except Exception as e:
            logger.error(f"Failed to load admin '{username}': {e}")

    user_dir = os.path.join(settings.keys_base_path, "users")
    for username in os.listdir(user_dir):
        if username in USER_DATA:
            continue
        settings_dict, pub_key = parse_key_file(os.path.join(user_dir, username))
        if pub_key:
            USER_DATA[username] = {
                "public_key": pub_key,
                "settings": settings_dict,
                "is_admin": False,
                "username": username,
            }

    for group_name in os.listdir(settings.groups_base_path):
        try:
            with open(os.path.join(settings.groups_base_path, group_name), "r") as f:
                group_settings = yaml.safe_load(f)
                if group_settings:
                    GROUP_DATA[group_name] = {
                        "settings": group_settings,
                        "name": group_name,
                    }
        except Exception as e:
            logger.error(f"Failed to load group {group_name}: {e}")

    logger.info(f"Loaded {len(USER_DATA)} user(s) and {len(GROUP_DATA)} group(s).")


def get_user(username: str) -> Optional[Dict]:
    return USER_DATA.get(username)


def get_effective_settings(username: str) -> Dict:
    """Returns the final, calculated settings for a user, including group overrides."""
    user = get_user(username)
    if not user or user.get("is_admin"):
        return DEFAULT_USER_SETTINGS.copy()

    base_settings = user.get("settings", DEFAULT_USER_SETTINGS.copy())
    group_name = base_settings.get("group", "none")

    if group_name and group_name != "none" and group_name in GROUP_DATA:
        group_settings = GROUP_DATA[group_name].get("settings", {})
        effective_settings = base_settings.copy()
        effective_settings.update(group_settings)
        return effective_settings

    return base_settings


def get_all_users() -> List[Dict]:
    return [u for u in USER_DATA.values() if not u["is_admin"]]


def get_all_admins() -> List[Dict]:
    return [u for u in USER_DATA.values() if u["is_admin"]]


def get_all_groups() -> List[Dict]:
    return list(GROUP_DATA.values())


def create_admin(
    username: str, public_key: Optional[str]
) -> Tuple[Dict, Optional[str]]:
    if not re.match(r"^[a-zA-Z0-9_-]+$", username):
        raise ValueError(
            "Invalid username. Use only letters, numbers, underscore, or hyphen."
        )
    if username in USER_DATA:
        raise ValueError(f"User or admin '{username}' already exists.")

    private_pem = None
    if public_key:
        public_pem = public_key
    else:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_key_obj = private_key.public_key()
        public_pem = public_key_obj.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    admin_dir = os.path.join(settings.keys_base_path, "admins")
    admin_file_path = os.path.join(admin_dir, username)

    with open(admin_file_path, "w") as f:
        f.write(public_pem.strip())
    os.chmod(admin_file_path, 0o600)

    load_users_and_groups()
    new_admin_data = get_user(username)
    return new_admin_data, private_pem


def delete_admin(username: str):
    if username == "admin":
        raise ValueError("The root 'admin' account cannot be deleted.")

    user = get_user(username)
    if not user or not user.get("is_admin"):
        raise ValueError(f"Admin '{username}' not found.")

    user_storage_path = os.path.join(settings.storage_path, username)
    if os.path.isdir(user_storage_path):
        shutil.rmtree(user_storage_path)
        logger.info(f"Deleted storage for admin '{username}'.")

    admin_file_path = os.path.join(settings.keys_base_path, "admins", username)
    if os.path.exists(admin_file_path):
        os.remove(admin_file_path)
        load_users_and_groups()
        logger.info(f"Deleted admin '{username}'.")
    else:
        raise ValueError(f"Admin file for '{username}' not found.")


def create_user(
    username: str, public_key: Optional[str], settings: dict
) -> Tuple[Dict, Optional[str]]:
    if not re.match(r"^[a-zA-Z0-9_-]+$", username):
        raise ValueError(
            "Invalid username. Use only letters, numbers, underscore, or hyphen."
        )
    if username in USER_DATA:
        raise ValueError(f"User '{username}' already exists.")
    private_pem = None
    if public_key:
        public_pem = public_key
    else:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        public_key_obj = private_key.public_key()
        public_pem = public_key_obj.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
    write_user_file(username, public_pem, settings)
    load_users_and_groups()
    new_user_data = get_user(username)
    return new_user_data, private_pem


def delete_user(username: str):
    user = get_user(username)
    if not user:
        raise ValueError(f"User '{username}' not found.")
    if user.get("is_admin"):
        raise ValueError("Cannot delete an admin user.")

    user_storage_path = os.path.join(settings.storage_path, username)
    if os.path.isdir(user_storage_path):
        shutil.rmtree(user_storage_path)
        logger.info(f"Deleted storage for user '{username}'.")

    base_path = os.path.join(settings.keys_base_path, "users")
    file_path = os.path.join(base_path, username)
    if os.path.exists(file_path):
        os.remove(file_path)
        load_users_and_groups()
        logger.info(f"Deleted user '{username}'.")
    else:
        raise ValueError(f"User file for '{username}' not found.")


def update_user_settings(username: str, new_settings: dict):
    user = get_user(username)
    if not user:
        raise ValueError(f"User '{username}' not found.")
    if user["is_admin"]:
        raise ValueError("Cannot update settings for an admin user.")
    write_user_file(username, user["public_key"], new_settings)
    load_users_and_groups()


def write_group_file(group_name: str, settings_dict: dict):
    file_path = os.path.join(settings.groups_base_path, group_name)
    with open(file_path, "w") as f:
        yaml.dump(settings_dict, f, default_flow_style=False)
    os.chmod(file_path, 0o600)
    logger.info(f"Wrote group file for '{group_name}'.")
    load_users_and_groups()


def delete_group(group_name: str):
    if group_name not in GROUP_DATA:
        raise ValueError(f"Group '{group_name}' not found.")
    file_path = os.path.join(settings.groups_base_path, group_name)
    if os.path.exists(file_path):
        os.remove(file_path)
        load_users_and_groups()
        logger.info(f"Deleted group '{group_name}'.")
    else:
        raise ValueError(f"Group file for '{group_name}' not found.")


def get_home_dirs(username: str) -> List[str]:
    """Lists home directories for a given user."""
    user_storage_path = os.path.join(settings.storage_path, username)
    if not os.path.isdir(user_storage_path):
        return []
    try:
        return sorted(
            [
                d
                for d in os.listdir(user_storage_path)
                if os.path.isdir(os.path.join(user_storage_path, d))
            ]
        )
    except OSError as e:
        logger.error(f"Error listing home directories for {username}: {e}")
        return []


def create_home_dir(username: str, home_name: str):
    """Creates a new home directory for a user."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", home_name):
        raise ValueError(
            "Invalid home directory name. Use only letters, numbers, underscore, or hyphen."
        )

    user_storage_path = os.path.join(settings.storage_path, username)
    new_home_path = os.path.join(user_storage_path, home_name)

    if os.path.exists(new_home_path):
        raise ValueError(
            f"Home directory '{home_name}' already exists for user '{username}'."
        )

    try:
        os.makedirs(new_home_path, exist_ok=True, mode=0o755)
        os.makedirs(
            os.path.join(new_home_path, "Desktop", "files"), exist_ok=True, mode=0o755
        )
        logger.info(f"Created home directory '{home_name}' for user '{username}'.")
    except OSError as e:
        logger.error(f"Failed to create home directory for {username}: {e}")
        raise


def delete_home_dir(username: str, home_name: str):
    """Deletes a home directory for a user."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", home_name):
        raise ValueError("Invalid home directory name.")

    home_path = os.path.join(settings.storage_path, username, home_name)

    if not os.path.isdir(home_path):
        raise ValueError(
            f"Home directory '{home_name}' not found for user '{username}'."
        )

    try:
        shutil.rmtree(home_path)
        logger.info(f"Deleted home directory '{home_name}' for user '{username}'.")
    except OSError as e:
        logger.error(f"Failed to delete home directory for {username}: {e}")
        raise
