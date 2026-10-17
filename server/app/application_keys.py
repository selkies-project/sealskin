"""Application keys: what the companion apps sign in with, and the root administrator's way into the web app.

A key is a secret a signed-in web user makes for the browser extension or the
mobile app, `ssk_<id>_<secret>@<host>:<port>`, the server's address after
the `@`, shown once; the app reads the address and the server ignores it. The
shared store keeps the hash
alone, in `cluster/application_keys.yml`, so a key works on every node of a
cluster and the store holds no secret. The record of a key names its user, a
name the user gave it, when it was made and when it expires, when and from
which client it was last used. `security.verify_token` takes a key as a
bearer on the plain lane; what a key may do is what its user may do.

What an administrator sets, through the users' and groups' settings
(`user_manager`):

* `application_keys`, whether the user may make and use keys at all.
* `application_key_clients`, which of the companion apps may use them, by
  the name each client gives itself in `CLIENT_HEADER`: `chrome`, `firefox`,
  `android`, `ios`, or `other` for anything else. The apps say who they are
  and nothing checks them; it is a convenience, not a lock.
* The apps reach the server under `/companion/`: the same pages and API, a
  prefix a reverse proxy that signs users in lets through, where
  `security.verify_token` takes a key and nothing else, and where the web
  sign-in and handshake routes answer 404.
* `application_key_days`, the longest a key may live; `-1` lets a key never
  expire, the default.
* `application_key_dormant_hours` and `application_key_follow_sign_in`, the
  dormancy rules: a key sleeps when its user's last web sign-in is older than
  the hours, or, with the switch, as soon as that sign-in has ended by the
  provider's or the proxy's clock. A dormant key answers `dormant`, and the
  app sends its user to the web app, whose sign-in wakes the keys
  (`user_manager.note_web_sign_in`).

The `root` administrator, who has no identity provider, signs in to the web
app with one of root's own keys (`web_sign_in`): the first start generates
one and writes it to `root_key_path` for the administrator to copy and delete,
or `SEALSKIN_ROOT_KEY` names it. Wrong keys at the web, and wrong bearers
of any shape but `ssk_`, which could be a guess at the environment's key, are
counted per address, five a minute, which bounds guessing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from typing import Any

from . import audit, persistence, store, user_manager
from .models import APP_KEY_CLIENTS
from .settings import settings

logger = logging.getLogger(__name__)

PREFIX = "ssk_"
#: The header a companion app names itself in, with a value of `APP_KEY_CLIENTS`; the proxy marks the address in `X-SealSkin-Client`.
CLIENT_HEADER = "X-SealSkin-App"
YAML_NAME = "application_keys.yml"
ENV_KEY_ID = "env"
ENV_KEY_NAME = "environment"
TRIES = 5
#: Seconds between two writes of a key's last use.
TOUCH_SECONDS = 600
MAX_FAILURE_SOURCES = 10000

_KEYS: dict[str, dict[str, Any]] = {}
_FAILURES: dict[str, list[float]] = {}


class KeyRefused(Exception):
    """A key that signs nobody in; `code` is what the client is told."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Keep the code the client reads and the detail the log gets."""
        super().__init__(detail or code)
        self.code = code


def _path() -> str:
    """Return the path of the shared record."""
    return os.path.join(settings.cluster_path, YAML_NAME)


def _hash(secret: str) -> str:
    """Return the hash the store keeps of a key's secret."""
    return hashlib.sha256(f"sealskin-key {secret}".encode()).hexdigest()


def parse(key: str) -> tuple[str, str] | None:
    """Split a key into its id and secret, or return `None` for text of another shape.

    The server's address an issued key carries after `@` is dropped.
    """
    key = key.strip().split("@", 1)[0]
    if not key.startswith(PREFIX):
        return None
    body = key[len(PREFIX) :]
    key_id, sep, secret = body.partition("_")
    if not sep or not key_id or not secret:
        return None
    return key_id, secret


def with_address(key: str, address: str) -> str:
    """Return a key with the server's address after it, as it is handed to the user."""
    host = address.split("://", 1)[-1].rstrip("/")
    return f"{key}@{host}" if host else key


def is_key(text: str) -> bool:
    """Whether a bearer is an application key by its shape."""
    return parse(text) is not None


def load() -> None:
    """Read the keys from the shared record."""
    data = persistence.read_yaml(_path(), {}) or {}
    _KEYS.clear()
    _KEYS.update({str(k): dict(v) for k, v in (data.get("keys") or {}).items() if isinstance(v, dict)})


def _save() -> None:
    """Write the keys to the shared record."""
    persistence.write_yaml_sync(_path(), {"keys": dict(_KEYS)})


def _mutate(change: Any) -> Any:
    """Apply `change` to the keys as the store holds them now and write them back, once more after a conflict."""
    for attempt in range(2):
        load()
        result = change()
        try:
            _save()
            return result
        except store.Conflict:
            if attempt:
                raise
    return None


def view(record: dict[str, Any]) -> dict[str, Any]:
    """Return a key's record as its user and the dashboard see it, with its `state`."""
    now = time.time()
    expires = record.get("expires")
    state = "active"
    if expires and expires <= now:
        state = "expired"
    elif _dormant(record["user"], now):
        state = "dormant"
    return {
        "id": record["id"],
        "name": record.get("name") or "",
        "created": record.get("created") or 0,
        "expires": expires,
        "last_used": record.get("last_used"),
        "client": record.get("client") or "",
        "state": state,
    }


def list_for(username: str) -> list[dict[str, Any]]:
    """Return the keys of a user, newest first."""
    keys = [dict(v, id=k) for k, v in _KEYS.items() if v.get("user") == username]
    return [view(record) for record in sorted(keys, key=lambda r: r.get("created") or 0, reverse=True)]


def policy(username: str) -> dict[str, Any]:
    """Return what the settings let a user do with keys, as the web app's keys section reads it."""
    effective = user_manager.get_effective_settings(username)
    stamp = user_manager.web_sign_in_of(username)
    return {
        "allowed": bool(effective.get("application_keys", True)),
        "clients": list(effective.get("application_key_clients") or []),
        "max_days": effective.get("application_key_days", -1),
        "dormant_hours": effective.get("application_key_dormant_hours", -1),
        "follow_sign_in": bool(effective.get("application_key_follow_sign_in")),
        "web_sign_in": stamp,
    }


def create(username: str, name: str, days: float | None, by: str = "") -> tuple[str, dict[str, Any]]:
    """Make a key for `username` and return the key text, shown once, with the record's view.

    Args:
        username: The key's user.
        name: What the user calls the key, as the device it is for.
        days: How long it lives, `None` for as long as the settings allow.
        by: Who made it, where not the user.

    Raises:
        KeyRefused: `disabled` where the settings deny the user keys, `days`
            for a lifetime over the longest the settings allow.
    """
    effective = user_manager.get_effective_settings(username)
    if not effective.get("application_keys", True):
        raise KeyRefused("disabled", f"'{username}' may not use application keys")
    longest = effective.get("application_key_days", -1)
    if days is not None and days <= 0:
        raise KeyRefused("days", "a key lives at least part of a day")
    if longest is not None and longest >= 0:
        if days is None or days > longest:
            if days is not None:
                raise KeyRefused("days", f"a key of '{username}' lives {longest} days at most")
            days = longest
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    now = time.time()
    record = {
        "user": username,
        "name": " ".join(name.split())[:60] or "Application key",
        "hash": _hash(secret),
        "created": now,
        "expires": now + days * 86400 if days is not None and days >= 0 else None,
        "last_used": None,
        "client": "",
    }

    def add() -> None:
        _KEYS[key_id] = record

    _mutate(add)
    audit.record("app_key_created", username, key=key_id, name=record["name"], by=by or None)
    logger.info("'%s' made the application key '%s' (%s).", by or username, record["name"], key_id)
    return f"{PREFIX}{key_id}_{secret}", view(dict(record, id=key_id))


def revoke(key_id: str, username: str | None = None, by: str = "") -> bool:
    """Take a key back; with `username`, only one of that user's. Returns whether one was there."""

    def drop() -> bool:
        record = _KEYS.get(key_id)
        if not record or (username is not None and record.get("user") != username):
            return False
        del _KEYS[key_id]
        audit.record("app_key_revoked", record["user"], key=key_id, name=record.get("name"), by=by or None)
        return True

    return bool(_mutate(drop))


def revoke_all(username: str) -> int:
    """Take back every key of a user, as when the account goes. Returns how many there were."""

    def drop() -> int:
        gone = [k for k, v in _KEYS.items() if v.get("user") == username]
        for key_id in gone:
            del _KEYS[key_id]
        return len(gone)

    count = _mutate(drop)
    if count:
        audit.record("app_key_revoked", username, count=count)
    return int(count or 0)


def _dormant(username: str, now: float) -> bool:
    """Whether a user's keys sleep under the dormancy rules, given the user's last web sign-in."""
    if username == user_manager.ROOT:
        return False
    effective = user_manager.get_effective_settings(username)
    follow = bool(effective.get("application_key_follow_sign_in"))
    hours = effective.get("application_key_dormant_hours", -1)
    hours = hours if isinstance(hours, int | float) and hours >= 0 else None
    if not follow and hours is None:
        return False
    stamp = user_manager.web_sign_in_of(username)
    if not stamp:
        return True
    if follow and float(stamp.get("until") or 0) <= now:
        return True
    return hours is not None and now - float(stamp.get("at") or 0) > hours * 3600


def _touch(key_id: str, client: str) -> None:
    """Record a key's use, at most once in `TOUCH_SECONDS` unless the client changed."""
    record = _KEYS.get(key_id)
    now = time.time()
    if record and record.get("client") == client and now - float(record.get("last_used") or 0) < TOUCH_SECONDS:
        return

    def stamp() -> None:
        current = _KEYS.get(key_id)
        if current:
            current["last_used"] = now
            current["client"] = client

    try:
        _mutate(stamp)
    except (store.Conflict, store.StoreUnavailable, OSError) as exc:
        logger.debug("Could not record the use of key %s: %s", key_id, exc)


def authenticate(key: str, client: str, address: str = "") -> dict[str, Any]:
    """Return the record of the key a companion app presents, or refuse it.

    Args:
        key: The bearer.
        client: What the app calls itself in `CLIENT_HEADER`; anything but a
            known name counts as `other`.
        address: The client's address, which wrong whole keys are counted by.

    A key of the `ssk_` shape is looked up by its id, which is unguessable;
    any other text is tried whole against root's keys, so the key
    `SEALSKIN_ROOT_KEY` names signs the apps in too, and wrong ones are
    counted per address as at the web sign-in.

    Raises:
        KeyRefused: `locked` after too many wrong whole keys from `address`,
            `unknown` for a key nobody holds, `expired`, `disabled` where the
            user's settings deny keys, `client` for an app the settings leave
            out, `dormant` while the user has to sign in on the web first.
    """
    parsed = parse(key)
    if parsed:
        key_id, secret = parsed
        record = _KEYS.get(key_id)
        if not record or not secrets.compare_digest(_hash(secret), str(record.get("hash") or "")):
            raise KeyRefused("unknown", f"no key {key_id}")
    else:
        # A key from the environment may have any shape: it is matched whole, among root's alone, and so can be guessed at.
        if locked_out(address):
            raise KeyRefused("locked", "too many wrong keys; try again in a minute")
        whole = _hash(key.strip())
        key_id = next((k for k, v in _root_records().items() if secrets.compare_digest(whole, str(v.get("hash") or ""))), None)
        if key_id is None:
            _count_failure(address)
            raise KeyRefused("unknown", "not an application key")
        record = _KEYS[key_id]
    now = time.time()
    if record.get("expires") and float(record["expires"]) <= now:
        raise KeyRefused("expired", f"key {key_id} expired")
    username = str(record["user"])
    effective = user_manager.get_effective_settings(username)
    if not effective.get("application_keys", True):
        raise KeyRefused("disabled", f"'{username}' may not use application keys")
    client = client.strip().lower() if client.strip().lower() in APP_KEY_CLIENTS else "other"
    if client not in (effective.get("application_key_clients") or []):
        raise KeyRefused("client", f"'{username}' may not use application keys from {client}")
    if _dormant(username, now):
        raise KeyRefused("dormant", f"the keys of '{username}' wait for a web sign-in")
    _touch(key_id, client)
    return dict(record, id=key_id)


def refuse(key: str, client: str, address: str, code: str) -> None:
    """Log a refused key."""
    parsed = parse(key)
    audit.record("sign_in_refused", via="app_key", key=parsed[0] if parsed else "", client=client, address=address, reason=code)


# --- The root administrator's key ---------------------------------------------


def _root_records() -> dict[str, dict[str, Any]]:
    """Return root's keys by id."""
    return {k: v for k, v in _KEYS.items() if v.get("user") == user_manager.ROOT}


def ensure_root_key() -> None:
    """Make sure root has a key, generating and writing one out the first time.

    `SEALSKIN_ROOT_KEY` names one, kept under `ENV_KEY_ID` and replaced
    there when it changes, or dropped when the setting goes; with none set and
    no key of root's in the store, one is generated and written to
    `root_key_path` for the administrator to copy and delete.
    """
    load()
    given = settings.root_key.strip()
    parsed = parse(given) if given else None
    env_hash = _hash(parsed[1] if parsed else given) if given else None
    env_id = parsed[0] if parsed else ENV_KEY_ID
    root_keys = _root_records()
    env_records = {k: v for k, v in root_keys.items() if v.get("name") == ENV_KEY_NAME}
    if given:
        if env_records.get(env_id, {}).get("hash") == env_hash and len(env_records) == 1:
            return

        def set_env() -> None:
            for key_id in [k for k, v in _KEYS.items() if v.get("user") == user_manager.ROOT and v.get("name") == ENV_KEY_NAME]:
                del _KEYS[key_id]
            _KEYS[env_id] = {
                "user": user_manager.ROOT,
                "name": ENV_KEY_NAME,
                "hash": env_hash,
                "created": time.time(),
                "expires": None,
                "last_used": None,
                "client": "",
            }

        _mutate(set_env)
        return
    if env_records:

        def drop_env() -> None:
            for key_id in list(env_records):
                _KEYS.pop(key_id, None)

        _mutate(drop_env)
        root_keys = _root_records()
    if root_keys:
        return
    key_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    try:

        def add() -> None:
            if not _root_records():
                _KEYS[key_id] = {
                    "user": user_manager.ROOT,
                    "name": "first start",
                    "hash": _hash(secret),
                    "created": time.time(),
                    "expires": None,
                    "last_used": None,
                    "client": "",
                }

        _mutate(add)
    except Exception as exc:  # noqa: BLE001 - another node wrote it first
        logger.info("The root key was set elsewhere: %s", exc)
        return
    if key_id not in _KEYS:
        return
    try:
        fd = os.open(settings.root_key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{PREFIX}{key_id}_{secret}\n")
        logger.warning(
            "Generated the root application key; sign in to the web app as 'root' with the content of %s, then delete that file.",
            settings.root_key_path,
        )
    except OSError as exc:
        logger.error("Could not write the root key to %s: %s", settings.root_key_path, exc)


def _count_failure(address: str) -> None:
    """Count a wrong key from `address`, and refuse the address after `TRIES` within a minute."""
    now = time.time()
    for source in list(_FAILURES):
        _FAILURES[source] = [stamp for stamp in _FAILURES[source] if now - stamp < 60]
        if not _FAILURES[source]:
            del _FAILURES[source]
    if len(_FAILURES) < MAX_FAILURE_SOURCES:
        _FAILURES.setdefault(address, []).append(now)


def locked_out(address: str) -> bool:
    """Whether `address` sent too many wrong keys within the last minute."""
    now = time.time()
    return len([stamp for stamp in _FAILURES.get(address, []) if now - stamp < 60]) >= TRIES


def web_sign_in(key: str, address: str = "") -> dict[str, Any]:
    """Return the identity of the web sign-in a root key starts, or refuse the key.

    Args:
        key: The key offered at the web app's sign-in page.
        address: The client's address, which wrong keys are counted by.

    Raises:
        KeyRefused: `locked` after too many wrong keys from `address`,
            `refused` for a key of nobody's, `notRoot` for another user's
            key, which signs the companion apps in alone, `expired`.
    """
    key = key.strip()
    if locked_out(address):
        raise KeyRefused("locked", "too many wrong keys; try again in a minute")
    load()
    parsed = parse(key)
    record = None
    if parsed and parsed[0] in _KEYS and secrets.compare_digest(_hash(parsed[1]), str(_KEYS[parsed[0]].get("hash") or "")):
        record = dict(_KEYS[parsed[0]], id=parsed[0])
    else:
        # A key from the environment may have any shape: it is matched whole, among root's alone.
        whole = _hash(key)
        for key_id, candidate in _root_records().items():
            if secrets.compare_digest(whole, str(candidate.get("hash") or "")):
                record = dict(candidate, id=key_id)
    if not record:
        _count_failure(address)
        audit.record("sign_in_refused", user_manager.ROOT, via="root", address=address)
        raise KeyRefused("refused", "the key is nobody's")
    if record.get("user") != user_manager.ROOT:
        raise KeyRefused("notRoot", "only root's keys sign in to the web app")
    now = time.time()
    if record.get("expires") and float(record["expires"]) <= now:
        raise KeyRefused("expired", f"key {record['id']} expired")
    _touch(record["id"], "web")
    return {
        "username": user_manager.ROOT,
        "admin": True,
        "groups": [],
        "subject": "root",
        "via": "root",
        "expires": now + settings.web_session_seconds,
    }
