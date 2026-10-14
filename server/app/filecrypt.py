"""Encryption of the users' shared files in a remote store.

With `store_key` set, a user's shared files are encrypted before they leave
the node and decrypted as they arrive, each user under a key of their own.
That key is random, made the first time the user's files are sent, and kept
in the store (`cluster/file_keys/<user>.yml`) wrapped under the cluster key
every node derives from `store_key`. So the bucket's keys alone read no
user's file, the cluster key alone reads none either, and deleting a user's
key, which deleting the user does, makes their files unreadable wherever a
copy of the bucket is.

A file is `MAGIC`, a nonce, and the AES-256-GCM ciphertext of its content
with `MAGIC` as the associated data. An object without the header was written
before the key was set and is read as it is; it is encrypted when next sent.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import persistence, store
from .settings import settings

logger = logging.getLogger(__name__)

MAGIC = b"SSKF1"
NONCE_BYTES = 12
_lock = threading.Lock()
_keys: dict[str, bytes] = {}


class Unreadable(Exception):
    """A file or key could not be decrypted: another cluster key wrote it, or it was tampered with."""


def enabled() -> bool:
    """Whether shared files are encrypted on their way to the store."""
    return bool(settings.store_key)


def _cluster_key() -> bytes:
    """Return the key every node derives from `store_key`."""
    return hashlib.sha256(b"sealskin-store-key " + settings.store_key.encode("utf-8")).digest()


def key_id() -> str:
    """Return what identifies the cluster key in a wrapped key, so a wrong key is told from a damaged one."""
    return hashlib.sha256(b"sealskin-store-key-id " + _cluster_key()).hexdigest()[:16]


def _path(username: str) -> str:
    """Return where a user's wrapped key is kept."""
    return os.path.join(settings.cluster_path, "file_keys", f"{username}.yml")


def _wrap(key: bytes) -> dict[str, str | float]:
    """Return the record that keeps `key` wrapped under the cluster key."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(_cluster_key()).encrypt(nonce, key, MAGIC)
    return {"key_id": key_id(), "wrapped": base64.b64encode(nonce + sealed).decode(), "created": time.time()}


def _unwrap(record: dict) -> bytes:
    """Return the key a record keeps.

    Raises:
        Unreadable: When the record was wrapped under another cluster key, or is damaged.
    """
    if record.get("key_id") != key_id():
        raise Unreadable("The user's file key was wrapped under another SEALSKIN_STORE_KEY.")
    try:
        raw = base64.b64decode(str(record.get("wrapped", "")))
        return AESGCM(_cluster_key()).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], MAGIC)
    except (ValueError, InvalidTag) as exc:
        raise Unreadable("The user's file key is damaged.") from exc


def user_key(username: str) -> bytes:
    """Return a user's file key, making and storing one the first time.

    Raises:
        Unreadable: When the stored key was wrapped under another cluster key.
        store.Conflict: When another node kept changing the record.
    """
    with _lock:
        known = _keys.get(username)
        if known is not None:
            return known
        path = _path(username)
        for _attempt in range(3):
            record = persistence.read_yaml(path, None)
            if isinstance(record, dict) and record.get("wrapped"):
                key = _unwrap(record)
                break
            key = secrets.token_bytes(32)
            try:
                persistence.write_yaml_sync(path, _wrap(key))
            except store.Conflict:
                continue
            break
        else:
            raise store.Conflict(path)
        _keys[username] = key
        return key


def seal(username: str, data: bytes) -> bytes:
    """Return `data` as it is sent to the store: encrypted under the user's key when `enabled`."""
    if not enabled():
        return data
    nonce = secrets.token_bytes(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(user_key(username)).encrypt(nonce, data, MAGIC)


def unseal(username: str, data: bytes) -> bytes:
    """Return the content of an object from the store, decrypting one that `seal` wrote.

    Raises:
        Unreadable: When the object is encrypted and this node cannot decrypt it.
    """
    if not data.startswith(MAGIC):
        return data
    if not enabled():
        raise Unreadable("The file is encrypted and this node has no SEALSKIN_STORE_KEY.")
    body = data[len(MAGIC) :]
    try:
        return AESGCM(user_key(username)).decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], MAGIC)
    except InvalidTag as exc:
        raise Unreadable("The file was encrypted under another key.") from exc


def forget(username: str) -> None:
    """Remove a user's file key, after which no copy of their files in the store can be read."""
    with _lock:
        _keys.pop(username, None)
    persistence.remove(_path(username))


def reset() -> None:
    """Drop the keys held in memory, as when the cluster's records changed elsewhere."""
    with _lock:
        _keys.clear()
