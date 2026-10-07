"""Users, administrators, and groups stored as flat files.

* `keys/admins/<username>`: the administrator's public key PEM.
* `keys/users/<username>`: a `--- Settings ---` YAML block followed by a
  `--- Public Key ---` PEM block, empty for a user who signs in only through
  an identity provider (see `sso.py`).
* `groups/<name>`: YAML settings its members get (see `get_effective_settings`).

A user a sign-in created while `sso_hold_new_users` was on carries
`approved: false` and is held (`held`) until placed in a group, approved by an
administrator (`approve`), or named in a group by the provider.

All three are objects of the shared store (`store.MOUNTS`), read into memory
after every change here and whenever the store reports one made elsewhere or
by hand. The `root` administrator has no file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any
from urllib.parse import urlsplit

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from . import audit, persistence, store
from .fsutil import safe_join
from .settings import settings

logger = logging.getLogger(__name__)

USER_DATA: dict[str, dict[str, Any]] = {}
GROUP_DATA: dict[str, dict[str, Any]] = {}

#: Name of the administrator that signs in with the root token and has no file.
ROOT = "root"

#: Keys of a user record the server writes and an administrator's edit keeps.
RECORD_KEYS = ("auth", "provider_groups", "approved")


class NoAccount(ValueError):
    """A sign-in for a user that does not exist while sign-ins create none."""

DEFAULT_USER_SETTINGS: dict[str, Any] = {
    "active": True,
    "group": "none",
    "groups": [],
    "admin": False,
    "persistent_storage": True,
    "public_sharing": False,
    "harden_container": False,
    "harden_openbox": False,
    "edit_templates": False,
    "gpu": True,
    "gpu_share": True,
    "home_migration": False,
    "storage_limit": -1,
    "session_limit": -1,
    "session_cpus": -1,
    "session_memory_mb": -1,
    "session_hours": -1,
    "allowance_hours": -1,
    "allowance_period": "month",
    "pools": [],
    "pools_denied": [],
    "proot_catalog": None,
}

#: Switches and the value of each that restricts: where groups disagree, that value wins.
SWITCHES: dict[str, bool] = {
    "active": False,
    "admin": False,
    "persistent_storage": False,
    "public_sharing": False,
    "edit_templates": False,
    "gpu": False,
    "gpu_share": False,
    "home_migration": False,
    "harden_container": True,
    "harden_openbox": True,
}

#: Limits, where a negative value sets none and the smallest set by any group wins.
LIMITS = ("storage_limit", "session_limit", "session_cpus", "session_memory_mb", "session_hours", "allowance_hours")

SERVER_PUBLIC_KEY_PEM: str | None = None
EXTERNAL_API_PORT: int = getattr(settings, "api_port", 8000)
EXTERNAL_SESSION_PORT: int = getattr(settings, "session_port", 8443)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def set_server_public_key(key: str) -> None:
    """Record the server public key used in generated admin config files."""
    global SERVER_PUBLIC_KEY_PEM
    SERVER_PUBLIC_KEY_PEM = key


def external_address() -> tuple[str, int | None]:
    """Split `HOST_URL` into the address clients use and the port it names, if any.

    A port there is the one clients reach both the API and sessions on, as
    behind an ingress; without one the discovered ports apply.
    """
    host_url = os.environ.get("HOST_URL", "HOST_URL")
    try:
        port = urlsplit(f"//{host_url}").port
    except ValueError:
        return host_url, None
    return (host_url.rsplit(":", 1)[0], port) if port else (host_url, None)


def set_external_ports(api_port: int, session_port: int) -> None:
    """Record the externally reachable ports used in generated config files."""
    global EXTERNAL_API_PORT, EXTERNAL_SESSION_PORT
    EXTERNAL_API_PORT = api_port
    EXTERNAL_SESSION_PORT = session_port


def _atomic_write(path: str, content: str, mode: int = 0o600) -> None:
    """Write `content` to `path` atomically with the given permissions."""
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
        `(private_pem, public_pem)`.
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


def _groups_of(settings_dict: dict[str, Any]) -> list[str]:
    """Return the groups a settings block names, the single `group` of older files included."""
    names = [str(g) for g in settings_dict.get("groups") or [] if g]
    single = settings_dict.get("group")
    if single and single != "none" and single not in names:
        names.insert(0, str(single))
    return names


def parse_key_file(path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a user file into settings and public key.

    Args:
        path: File under `keys/users`.

    Returns:
        `(settings, public_key_pem)`, the key empty for a user with none; both
        `None` when unreadable.
    """
    try:
        raw = persistence.read_bytes(path)
        if raw is None:
            return None, None
        parts = raw.decode("utf-8").split("--- Public Key ---")
        settings_yaml = parts[0].replace("--- Settings ---", "").strip()
        pub_key_pem = parts[1].strip() if len(parts) > 1 else ""
        user_settings = yaml.safe_load(settings_yaml) if settings_yaml else {}
        final_settings = DEFAULT_USER_SETTINGS.copy()
        if user_settings:
            final_settings.update(user_settings)
        final_settings["groups"] = _groups_of(final_settings)
        final_settings["group"] = final_settings["groups"][0] if final_settings["groups"] else "none"
        return final_settings, pub_key_pem
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to parse user file %s: %s", path, exc)
        return None, None


def write_user_file(username: str, pub_key_pem: str, settings_dict: dict[str, Any]) -> None:
    """Write (or replace) a user's settings and public key file.

    Args:
        username: The user.
        pub_key_pem: Public key PEM, empty for a user who signs in without one.
        settings_dict: Settings to store.
    """
    file_path = safe_join(settings.keys_base_path, "users", username)
    stored = dict(settings_dict)
    stored["groups"] = _groups_of(stored)
    stored["group"] = stored["groups"][0] if stored["groups"] else "none"
    settings_yaml = yaml.safe_dump(stored, default_flow_style=False, sort_keys=False)
    content = (
        "--- Settings ---\n"
        f"{settings_yaml.strip()}\n"
        "--- Public Key ---\n"
        f"{pub_key_pem.strip()}\n"
    )
    persistence.write_bytes(file_path, content.encode("utf-8"))
    logger.info("Wrote user file for '%s' at %s", username, file_path)


def _generate_default_admin() -> None:
    """Create the default key-file `admin` account when no administrator exists.

    The generated private key is written to `admin.json` three levels above
    the keys directory (`/config/admin.json` by default) for the operator to
    import into a client and then delete. A node that takes no key-file
    clients creates none; its first administrator is `root`.
    """
    admin_dir = os.path.join(settings.keys_base_path, "admins")
    if not settings.legacy_auth or persistence.list_names(admin_dir):
        return

    logger.warning("No admin users found. Creating a default 'admin' user.")
    private_pem, public_pem = _generate_key_pair(4096)
    try:
        persistence.write_bytes(os.path.join(admin_dir, "admin"), public_pem.encode("utf-8"))
    except store.Conflict:
        return

    config_path = os.path.abspath(os.path.join(settings.keys_base_path, "..", "..", "..", "admin.json"))
    address, port = external_address()
    admin_config = {
        "server_endpoint": address,
        "api_port": port or EXTERNAL_API_PORT,
        "session_port": port or EXTERNAL_SESSION_PORT,
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
    """Re-read the users, administrators, and groups into memory."""
    logger.info("Reloading users, admins, and groups...")
    admin_dir = os.path.join(settings.keys_base_path, "admins")
    user_dir = os.path.join(settings.keys_base_path, "users")
    if store.is_local():
        os.makedirs(admin_dir, exist_ok=True)
        os.makedirs(user_dir, exist_ok=True)
        os.makedirs(settings.groups_base_path, exist_ok=True)

    _generate_default_admin()

    users: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {}
    for username in persistence.list_names(admin_dir):
        try:
            raw = persistence.read_bytes(os.path.join(admin_dir, username))
            pub_key = raw.decode("utf-8").strip() if raw else ""
            if pub_key:
                users[username] = {"public_key": pub_key, "is_admin": True, "username": username}
        except (OSError, store.StoreUnavailable) as exc:
            logger.error("Failed to load admin '%s': %s", username, exc)

    for username in persistence.list_names(user_dir):
        if username in users or username == ROOT:
            continue
        settings_dict, pub_key = parse_key_file(os.path.join(user_dir, username))
        if settings_dict is not None and pub_key is not None:
            users[username] = {
                "public_key": pub_key,
                "settings": settings_dict,
                "is_admin": False,
                "username": username,
            }

    for group_name in persistence.list_names(settings.groups_base_path):
        try:
            group_settings = persistence.read_yaml(os.path.join(settings.groups_base_path, group_name))
            if isinstance(group_settings, dict):
                groups[group_name] = {"settings": group_settings, "name": group_name}
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to load group %s: %s", group_name, exc)

    # Never empty in between: requests are answered while a reload runs.
    USER_DATA.update(users)
    for name in [n for n in USER_DATA if n not in users]:
        del USER_DATA[name]
    GROUP_DATA.update(groups)
    for name in [n for n in GROUP_DATA if n not in groups]:
        del GROUP_DATA[name]
    logger.info("Loaded %d user(s) and %d group(s).", len(USER_DATA), len(GROUP_DATA))


def get_user(username: str) -> dict[str, Any] | None:
    """Return a user record by name; `root` has one without a file."""
    if username == ROOT:
        return {"public_key": "", "is_admin": True, "username": ROOT}
    return USER_DATA.get(username)


def groups_of(username: str, provider_groups: Any = None) -> list[str]:
    """Return the groups a user is in: those the user file names, then those a sign-in brought.

    Args:
        username: The user.
        provider_groups: Groups an identity provider or proxy named at sign-in.
            Each admits the user to the group of that name and to every group
            listing it under `sso_groups`. `None` takes those of the last
            sign-in, kept on the record.

    Returns:
        Names of existing groups, without repeats.
    """
    user = USER_DATA.get(username) or {}
    stored = user.get("settings") or {}
    if provider_groups is None:
        provider_groups = stored.get("provider_groups") or ()
    names = [g for g in _groups_of(stored) if g in GROUP_DATA]
    brought = {str(g).strip().lstrip("/") for g in provider_groups or () if str(g).strip()}
    for name, group in GROUP_DATA.items():
        mapped = {str(g).strip().lstrip("/") for g in group["settings"].get("sso_groups") or []}
        if name not in names and (name in brought or mapped & brought):
            names.append(name)
    return names


def held(username: str, provider_groups: Any = None) -> bool:
    """Return whether a user waits for an administrator: created held, in no group, and not approved.

    Args:
        username: The user.
        provider_groups: See `groups_of`.
    """
    user = USER_DATA.get(username)
    if not user or user.get("is_admin") or not settings.sso_hold_new_users:
        return False
    stored = user.get("settings") or {}
    return stored.get("approved") is False and not stored.get("admin") and not groups_of(username, provider_groups)


def get_effective_settings(username: str, provider_groups: Any = None) -> dict[str, Any]:
    """Return the settings a user's sessions and requests run under.

    A user in no group has the settings of the user file. In groups, each
    setting is taken from the groups that set it: a switch takes its
    restricting value when any group gives it that (see `SWITCHES`), a limit
    the smallest any group sets, and the pools are those any group allows
    less those any denies. A setting no group sets stays the user's own.
    Administrators always get the defaults, short of the PRoot Apps catalog,
    which is the user's own or the first of their groups' to name one.

    Args:
        username: The user.
        provider_groups: See `groups_of`.
    """
    user = get_user(username)
    if not user:
        return dict(DEFAULT_USER_SETTINGS)
    base = DEFAULT_USER_SETTINGS.copy()
    base.update(user.get("settings") or {})
    names = groups_of(username, provider_groups)
    members = [GROUP_DATA[name]["settings"] for name in names]
    # A catalog is a choice, not a limit: the user's own, else the first group's that makes one.
    catalog = base.get("proot_catalog") or next((g["proot_catalog"] for g in members if g.get("proot_catalog")), None)
    if user.get("is_admin"):
        return dict(DEFAULT_USER_SETTINGS, admin=True, proot_catalog=catalog)
    effective = dict(base, groups=names, group=names[0] if names else "none", proot_catalog=catalog)
    for key, restricting in SWITCHES.items():
        chosen = [bool(g[key]) for g in members if g.get(key) is not None]
        if chosen:
            effective[key] = restricting if restricting in chosen else not restricting
    for key in LIMITS:
        chosen = [g[key] for g in members if isinstance(g.get(key), int | float) and g[key] >= 0]
        if chosen:
            effective[key] = min(chosen)
            if key == "allowance_hours":
                source = next(g for g in members if g.get(key) == effective[key])
                effective["allowance_period"] = source.get("allowance_period") or base["allowance_period"]
    allowed = {str(p) for g in members for p in g.get("pools") or []}
    effective["pools"] = sorted(allowed | {str(p) for p in base.get("pools") or []})
    effective["pools_denied"] = sorted(
        {str(p) for g in members for p in g.get("pools_denied") or []}
        | {str(p) for p in base.get("pools_denied") or []}
    )
    return effective


def ensure_user(username: str, via: str, subject: str = "", provider_groups: Any = ()) -> dict[str, Any]:
    """Return the user a sign-in vouches for, creating or binding the record where needed.

    The first sign-in through `via` binds the user to the provider's
    `subject`, and the groups the provider named are kept on the record for
    the dashboard.

    A user created while `sso_hold_new_users` is on is held until an
    administrator places them in a group or approves them (see `held`).

    Args:
        username: Name the provider or proxy gave.
        via: `oidc`, `saml`, or `proxy`.
        subject: The provider's stable name for the account; empty for a proxy.
        provider_groups: Groups the provider named.

    Returns:
        The user record.

    Raises:
        NoAccount: For an unknown user while `sso_create_users` is off.
        ValueError: For a name SealSkin cannot use, a key-file
            administrator's, or a user bound to another subject.
    """
    validate_name(username)
    if username == ROOT:
        raise ValueError("The root administrator signs in with the root token alone.")
    user = USER_DATA.get(username)
    if user and user.get("is_admin"):
        raise ValueError(f"'{username}' is a key-file administrator.")
    if not user and not settings.sso_create_users:
        raise NoAccount(f"No user '{username}' exists and sign-ins create none.")
    current = dict(user["settings"]) if user else DEFAULT_USER_SETTINGS.copy()
    if not user and settings.sso_hold_new_users:
        current["approved"] = False
    bound = dict(current.get("auth") or {})
    if subject and bound.get(via, subject) != subject:
        raise ValueError(f"'{username}' is bound to another account of the provider.")
    listed = sorted({str(g).strip().lstrip("/") for g in provider_groups or () if str(g).strip()})
    wanted = dict(bound, **({via: subject} if subject else {}))
    if user and wanted == bound and listed == sorted(current.get("provider_groups") or []):
        return user
    current["auth"] = wanted
    current["provider_groups"] = listed
    write_user_file(username, user["public_key"] if user else "", current)
    load_users_and_groups()
    if not user:
        logger.info("Created user '%s' at its first sign-in through %s.", username, via)
        if held(username, provider_groups):
            audit.record("user_held", username, via=via, groups=listed)
            logger.info("'%s' is held until an administrator places the user in a group.", username)
    return USER_DATA[username]


def approve(username: str) -> dict[str, Any]:
    """Let a held user in without a group.

    Raises:
        ValueError: For an unknown user.
    """
    user = USER_DATA.get(username)
    if not user:
        raise ValueError(f"User '{username}' not found.")
    current = dict(user.get("settings") or {})
    if current.get("approved") is not True:
        current["approved"] = True
        write_user_file(username, user["public_key"], current)
        load_users_and_groups()
    return USER_DATA[username]


def get_all_users() -> list[dict[str, Any]]:
    """Return every non-admin user."""
    return [u for u in USER_DATA.values() if not u["is_admin"]]


def get_all_admins() -> list[dict[str, Any]]:
    """Return every administrator."""
    return [u for u in USER_DATA.values() if u["is_admin"]]


def get_all_groups() -> list[dict[str, Any]]:
    """Return every group."""
    return list(GROUP_DATA.values())


def validate_name(username: str) -> None:
    """Raise `ValueError` for names that are not filesystem safe."""
    if not _NAME_RE.fullmatch(username or ""):
        raise ValueError("Invalid username. Use only letters, numbers, underscore, or hyphen.")


def create_admin(username: str, public_key: str | None) -> tuple[dict[str, Any], str | None]:
    """Create an administrator.

    Args:
        username: New admin name.
        public_key: Public key PEM, or `None` to generate a key pair.

    Returns:
        `(user_record, private_key_pem_or_None)`.

    Raises:
        ValueError: For invalid or duplicate names.
    """
    validate_name(username)
    if username in USER_DATA or username == ROOT:
        raise ValueError(f"User or admin '{username}' already exists.")
    private_pem: str | None = None
    if public_key:
        public_pem = public_key
    else:
        private_pem, public_pem = _generate_key_pair()
    persistence.write_bytes(
        safe_join(settings.keys_base_path, "admins", username), public_pem.strip().encode("utf-8")
    )
    load_users_and_groups()
    return get_user(username), private_pem


def delete_storage(username: str) -> None:
    """Remove the storage this node holds for `username`, home directories included."""
    path = safe_join(settings.storage_path, username)
    if os.path.isdir(path):
        shutil.rmtree(path)
        logger.info("Deleted storage for '%s'.", username)


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

    delete_storage(username)
    persistence.remove(safe_join(settings.keys_base_path, "admins", username))
    load_users_and_groups()
    logger.info("Deleted admin '%s'.", username)


def create_user(
    username: str, public_key: str | None, settings_dict: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """Create a user.

    Args:
        username: New user name.
        public_key: Public key PEM, or `None` to generate a key pair.
        settings_dict: Initial settings.

    Returns:
        `(user_record, private_key_pem_or_None)`.

    Raises:
        ValueError: For invalid or duplicate names.
    """
    validate_name(username)
    if username in USER_DATA or username == ROOT:
        raise ValueError(f"User '{username}' already exists.")
    private_pem: str | None = None
    if public_key is not None:
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

    delete_storage(username)
    persistence.remove(safe_join(settings.keys_base_path, "users", username))
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
    kept = {k: v for k, v in (user.get("settings") or {}).items() if k in RECORD_KEYS}
    write_user_file(username, user["public_key"], {**new_settings, **kept})
    load_users_and_groups()


def write_group_file(group_name: str, settings_dict: dict[str, Any]) -> None:
    """Create or replace a group file and reload.

    Args:
        group_name: Group name (validated by the API model).
        settings_dict: Settings applied to members.
    """
    file_path = safe_join(settings.groups_base_path, group_name)
    persistence.write_yaml_sync(file_path, {k: v for k, v in settings_dict.items() if v is not None})
    logger.info("Wrote group file for '%s'.", group_name)
    load_users_and_groups()


def delete_group(group_name: str) -> None:
    """Delete a group.

    Raises:
        ValueError: For unknown groups.
    """
    if group_name not in GROUP_DATA:
        raise ValueError(f"Group '{group_name}' not found.")
    persistence.remove(safe_join(settings.groups_base_path, group_name))
    load_users_and_groups()
    logger.info("Deleted group '%s'.", group_name)


def get_home_dirs(username: str) -> list[str]:
    """List a user's home directories (sub-directories of their storage)."""
    try:
        user_storage_path = safe_join(settings.storage_path, username)
    except ValueError:
        return []
    if not username or not os.path.isdir(user_storage_path):
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
    if not _NAME_RE.fullmatch(home_name or ""):
        raise ValueError("Invalid home directory name. Use only letters, numbers, underscore, or hyphen.")
    new_home_path = safe_join(settings.storage_path, username, home_name)
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
    if not _NAME_RE.fullmatch(home_name or ""):
        raise ValueError("Invalid home directory name.")
    home_path = safe_join(settings.storage_path, username, home_name)
    if not os.path.isdir(home_path):
        raise ValueError(f"Home directory '{home_name}' not found for user '{username}'.")
    try:
        shutil.rmtree(home_path)
        logger.info("Deleted home directory '%s' for user '%s'.", home_name, username)
    except OSError as exc:
        logger.error("Failed to delete home directory for %s: %s", username, exc)
        raise
