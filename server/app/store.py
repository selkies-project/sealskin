"""The object store that holds everything the nodes of a cluster share.

Users, groups, installed apps, app stores, templates, and the cluster's own
records (`cluster/`) are objects under fixed keys. Every node reads and writes
them through one `ObjectStore`, so the store is the authority and no node is:

* `FileStore` keeps each object as the file SealSkin has always kept it in, so
  a single server stores what it stored before and an administrator can still
  edit it by hand.
* `S3Store` keeps them in a bucket of any S3-compatible service.
* `PeerStore` reads and writes the `FileStore` of another node, for a small
  cluster with no bucket.

A write may be conditional on the object's entity tag (`if_match`) or on its
absence (`if_absent`), which is all the locking the administrative writes
need. Calls block: reads are served from the caches the callers keep, and
writes are administrative and rare.

A remote store is mirrored into the local files as it is read, and a node
that cannot reach it serves that mirror until it can.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from xml.etree import ElementTree

import httpx

from .settings import settings

logger = logging.getLogger(__name__)

#: Key prefixes (or whole keys) of the shared objects, and the setting naming where a
#: `FileStore` keeps each.
MOUNTS: dict[str, str] = {
    "installed_apps.yml": "installed_apps_path",
    "app_stores.yml": "app_stores_path",
    "app_templates/": "app_templates_path",
    "keys/": "keys_base_path",
    "groups/": "groups_base_path",
    "cluster/": "cluster_path",
    "files/": "shared_files_path",
}

#: Prefixes `changed` watches: everything shared but the users' files, which sync on their own.
WATCHED = tuple(prefix for prefix in MOUNTS if prefix != "files/")


class Conflict(Exception):
    """A conditional write found the object changed, or already there."""


class StoreUnavailable(Exception):
    """The store could not be reached."""


def key_for(path: str) -> str | None:
    """Return the key of the shared object kept at `path`, or `None` for a node's own file."""
    absolute = os.path.abspath(path)
    for prefix, setting in MOUNTS.items():
        root = os.path.abspath(getattr(settings, setting))
        if prefix.endswith("/"):
            if absolute == root:
                return prefix
            if absolute.startswith(root + os.sep):
                return prefix + absolute[len(root) + 1 :].replace(os.sep, "/")
        elif absolute == root:
            return prefix
    return None


def path_for(key: str) -> str:
    """Return the local file that holds, or mirrors, the object `key` names.

    Raises:
        ValueError: For a key outside the shared prefixes or one that steps out of its mount.
    """
    for prefix, setting in MOUNTS.items():
        root = os.path.abspath(getattr(settings, setting))
        if not prefix.endswith("/"):
            if key == prefix:
                return root
            continue
        if key.startswith(prefix):
            parts = key[len(prefix) :].split("/")
            if not all(parts) or any(part in (".", "..") or part.startswith(".tmp-") for part in parts):
                break
            return os.path.join(root, *parts)
    raise ValueError(f"'{key}' is not a shared object.")


class ObjectStore(ABC):
    """A flat keyed store with conditional writes."""

    @abstractmethod
    def get(self, key: str) -> tuple[bytes, str] | None:
        """Return an object's bytes and entity tag, or `None` when it does not exist."""

    @abstractmethod
    def put(self, key: str, data: bytes, if_match: str | None = None, if_absent: bool = False) -> str:
        """Write an object and return its new entity tag.

        Args:
            key: Object key.
            data: Its new content.
            if_match: Write only while the object still has this entity tag.
            if_absent: Write only when the object does not exist.

        Raises:
            Conflict: When the condition does not hold.
        """

    @abstractmethod
    def delete(self, key: str, if_match: str | None = None) -> None:
        """Remove an object; a missing one is not an error.

        Raises:
            Conflict: When `if_match` names another entity tag than the object's.
        """

    @abstractmethod
    def list(self, prefix: str = "") -> dict[str, str]:
        """Return the entity tag of every object whose key starts with `prefix`."""


class FileStore(ObjectStore):
    """Objects as the files under the paths `MOUNTS` names."""

    def __init__(self) -> None:
        """Start with an empty entity tag cache."""
        self._lock = threading.RLock()
        self._tags: dict[str, tuple[int, int, str]] = {}

    def _tag(self, path: str) -> str | None:
        """Return the content hash of `path`, rehashing only when its size or time changed."""
        try:
            stat = os.stat(path)
        except OSError:
            self._tags.pop(path, None)
            return None
        cached = self._tags.get(path)
        if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        try:
            with open(path, "rb") as handle:
                tag = hashlib.sha256(handle.read()).hexdigest()
        except OSError:
            return None
        self._tags[path] = (stat.st_mtime_ns, stat.st_size, tag)
        return tag

    def get(self, key: str) -> tuple[bytes, str] | None:
        """Read the file behind `key`."""
        try:
            with open(path_for(key), "rb") as handle:
                data = handle.read()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            return None
        return data, hashlib.sha256(data).hexdigest()

    def put(self, key: str, data: bytes, if_match: str | None = None, if_absent: bool = False) -> str:
        """Replace the file behind `key` atomically, private to the server's user."""
        path = path_for(key)
        directory = os.path.dirname(path)
        with self._lock:
            current = self._tag(path)
            if (if_absent and current is not None) or (if_match is not None and current != if_match):
                raise Conflict(key)
            os.makedirs(directory, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_path, 0o600)
                os.replace(temp_path, path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
            tag = hashlib.sha256(data).hexdigest()
            stat = os.stat(path)
            self._tags[path] = (stat.st_mtime_ns, stat.st_size, tag)
        return tag

    def delete(self, key: str, if_match: str | None = None) -> None:
        """Remove the file behind `key`."""
        path = path_for(key)
        with self._lock:
            current = self._tag(path)
            if current is None:
                return
            if if_match is not None and current != if_match:
                raise Conflict(key)
            os.remove(path)
            self._tags.pop(path, None)

    def list(self, prefix: str = "") -> dict[str, str]:
        """Walk the mounts `prefix` reaches."""
        found: dict[str, str] = {}
        for mount, setting in MOUNTS.items():
            if not (mount.startswith(prefix) or prefix.startswith(mount)):
                continue
            root = os.path.abspath(getattr(settings, setting))
            if not mount.endswith("/"):
                tag = self._tag(root)
                if tag:
                    found[mount] = tag
                continue
            for directory, subdirs, files in os.walk(root):
                subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
                for name in sorted(files):
                    if name.startswith("."):
                        continue
                    path = os.path.join(directory, name)
                    key = mount + os.path.relpath(path, root).replace(os.sep, "/")
                    if key.startswith(prefix) and (tag := self._tag(path)):
                        found[key] = tag
        return found


class S3Store(ObjectStore):
    """Objects in a bucket of an S3-compatible service, addressed path-style.

    Requests are signed with AWS Signature Version 4. Conditional writes use
    `If-Match` and `If-None-Match`, which the service must honor.
    """

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        prefix: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the store to a bucket.

        Args:
            endpoint: Base URL of the service, `https://s3.example.com`.
            bucket: Bucket name.
            prefix: Key prefix every object is kept under; may be empty.
            access_key: Access key id.
            secret_key: Secret access key.
            region: Region the signature names.
            transport: Transport override for tests.
        """
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self._client = httpx.Client(timeout=15, transport=transport)

    def _sign(self, method: str, path: str, query: dict[str, str], headers: dict[str, str], body: bytes) -> None:
        """Add the Signature Version 4 headers for a request to `headers`."""
        now = datetime.datetime.now(datetime.UTC)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        headers["host"] = urlsplit(self.endpoint).netloc
        headers["x-amz-date"] = stamp
        headers["x-amz-content-sha256"] = hashlib.sha256(body).hexdigest()
        signed = sorted(k.lower() for k in headers)
        lowered = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
        canonical_query = "&".join(
            f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items())
        )
        canonical = "\n".join(
            [
                method,
                quote(path, safe="/-_.~"),
                canonical_query,
                "".join(f"{k}:{lowered[k]}\n" for k in signed),
                ";".join(signed),
                headers["x-amz-content-sha256"],
            ]
        )
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(
            ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
        )
        key = f"AWS4{self.secret_key}".encode()
        for part in (day, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={';'.join(signed)}, Signature={signature}"
        )

    def _request(
        self,
        method: str,
        key: str | None,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> httpx.Response:
        """Send a signed request for an object, or for the bucket when `key` is `None`."""
        path = urlsplit(self.endpoint).path.rstrip("/") + f"/{self.bucket}"
        if key is not None:
            path += f"/{self.prefix}{key}"
        query = query or {}
        headers = dict(headers or {})
        self._sign(method, path, query, headers, body)
        headers.pop("host")
        origin = self.endpoint[: len(self.endpoint) - len(urlsplit(self.endpoint).path.rstrip("/"))]
        url = httpx.URL(origin + quote(path, safe="/-_.~"), params=query or None)
        try:
            return self._client.request(method, url, headers=headers, content=body)
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"The object store did not answer: {exc}") from exc

    @staticmethod
    def _check(response: httpx.Response, key: str) -> None:
        """Raise for a refused condition or any other failure."""
        if response.status_code in (409, 412):
            raise Conflict(key)
        if response.status_code >= 500:
            raise StoreUnavailable(f"The object store answered {response.status_code} for '{key}'.")
        if response.status_code >= 400:
            raise OSError(f"The object store refused '{key}': {response.status_code} {response.text[:200]}")

    def get(self, key: str) -> tuple[bytes, str] | None:
        """Fetch an object."""
        response = self._request("GET", key)
        if response.status_code == 404:
            return None
        self._check(response, key)
        return response.content, response.headers.get("etag", "")

    def put(self, key: str, data: bytes, if_match: str | None = None, if_absent: bool = False) -> str:
        """Upload an object, conditionally when asked."""
        headers = {}
        if if_absent:
            headers["if-none-match"] = "*"
        elif if_match is not None:
            headers["if-match"] = if_match
        response = self._request("PUT", key, headers=headers, body=data)
        if response.status_code == 404 and if_match is not None:
            raise Conflict(key)
        self._check(response, key)
        return response.headers.get("etag", "")

    def delete(self, key: str, if_match: str | None = None) -> None:
        """Delete an object, after comparing its entity tag when asked."""
        if if_match is not None:
            head = self._request("HEAD", key)
            if head.status_code == 404:
                return
            self._check(head, key)
            if head.headers.get("etag", "") != if_match:
                raise Conflict(key)
        response = self._request("DELETE", key)
        if response.status_code != 404:
            self._check(response, key)

    def list(self, prefix: str = "") -> dict[str, str]:
        """List the bucket under the store's prefix, following continuation tokens."""
        found: dict[str, str] = {}
        token = ""
        while True:
            query = {"list-type": "2", "prefix": self.prefix + prefix}
            if token:
                query["continuation-token"] = token
            response = self._request("GET", None, query=query)
            self._check(response, prefix)
            root = ElementTree.fromstring(response.content)
            namespace = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
            for item in root.iter(f"{namespace}Contents"):
                name = item.findtext(f"{namespace}Key") or ""
                if name.startswith(self.prefix) and not name.endswith("/"):
                    found[name[len(self.prefix) :]] = item.findtext(f"{namespace}ETag") or ""
            token = root.findtext(f"{namespace}NextContinuationToken") or ""
            if root.findtext(f"{namespace}IsTruncated") != "true" or not token:
                return found


class PeerStore(ObjectStore):
    """The `FileStore` of another node, reached over the peer channel."""

    def __init__(self, call: Callable[..., httpx.Response]) -> None:
        """Bind the store to a peer.

        Args:
            call: `call(method, path, headers=None, content=b"")`, which signs
                and sends a request to the node that keeps the store.
        """
        self._call = call

    def _send(self, method: str, key: str, headers: dict[str, str] | None = None, body: bytes = b"") -> httpx.Response:
        """Send a store request to the peer and map its refusals."""
        try:
            response = self._call(method, "/peer/store/" + quote(key, safe="/-_.~"), headers=headers, content=body)
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"The node that keeps the store did not answer: {exc}") from exc
        if response.status_code == 412:
            raise Conflict(key)
        if response.status_code >= 500 or response.status_code in (401, 403):
            raise StoreUnavailable(f"The node that keeps the store answered {response.status_code}.")
        if response.status_code >= 400 and response.status_code != 404:
            raise OSError(f"The node that keeps the store refused '{key}': {response.status_code}")
        return response

    def get(self, key: str) -> tuple[bytes, str] | None:
        """Fetch an object from the peer."""
        response = self._send("GET", key)
        if response.status_code == 404:
            return None
        return response.content, response.headers.get("etag", "")

    def put(self, key: str, data: bytes, if_match: str | None = None, if_absent: bool = False) -> str:
        """Write an object on the peer."""
        headers = {"if-none-match": "*"} if if_absent else {"if-match": if_match} if if_match is not None else {}
        return self._send("PUT", key, headers, data).headers.get("etag", "")

    def delete(self, key: str, if_match: str | None = None) -> None:
        """Delete an object on the peer."""
        self._send("DELETE", key, {"if-match": if_match} if if_match is not None else {})

    def list(self, prefix: str = "") -> dict[str, str]:
        """List the peer's objects."""
        response = self._send("GET", "", {"x-sealskin-list": prefix or "*"})
        return dict(response.json())


class MirroredStore(ObjectStore):
    """A remote store whose reads are copied into the local files and served from them when it is away.

    A read of an object the last listing showed unchanged since it was
    mirrored is served from the mirror, so reloading many objects costs one
    listing. After a failure the remote store is left alone for
    `RETRY_SECONDS`, so a store that is away does not hold up every read.
    """

    RETRY_SECONDS = 10.0
    FRESH_SECONDS = 5.0

    def __init__(self, remote: ObjectStore) -> None:
        """Wrap `remote`."""
        self.remote = remote
        self.local = FileStore()
        self.reachable = True
        self._retry_at = 0.0
        self._listed: dict[str, tuple[float, dict[str, str]]] = {}
        self._tags: dict[str, tuple[str, float]] = {}
        self._mirrored: dict[str, str] = {}

    def _away(self, exc: Exception) -> None:
        """Note that the remote store stopped answering."""
        if self.reachable:
            logger.error("%s Serving the local copy until it answers again.", exc)
        self.reachable = False
        self._retry_at = time.monotonic() + self.RETRY_SECONDS

    def _resting(self) -> bool:
        """Whether the remote store failed too recently to be asked again."""
        return not self.reachable and time.monotonic() < self._retry_at

    def get(self, key: str) -> tuple[bytes, str] | None:
        """Read through to the remote store, falling back to the mirror."""
        if self._resting():
            return self.local.get(key)
        known = self._tags.get(key)
        if known and self._mirrored.get(key) == known[0] and time.monotonic() - known[1] < self.FRESH_SECONDS:
            held = self.local.get(key)
            if held is not None:
                return held[0], known[0]
        try:
            found = self.remote.get(key)
        except StoreUnavailable as exc:
            self._away(exc)
            return self.local.get(key)
        self.reachable = True
        try:
            if found is None:
                self.local.delete(key)
                self._mirrored.pop(key, None)
            else:
                self.local.put(key, found[0])
                self._mirrored[key] = found[1]
        except (OSError, ValueError) as exc:
            logger.warning("Could not mirror '%s': %s", key, exc)
        return found

    def put(self, key: str, data: bytes, if_match: str | None = None, if_absent: bool = False) -> str:
        """Write to the remote store, then to the mirror."""
        try:
            tag = self.remote.put(key, data, if_match=if_match, if_absent=if_absent)
        except Conflict:
            # Someone else wrote it: what this node holds of it is stale.
            self._tags.pop(key, None)
            self._listed.clear()
            raise
        self.reachable = True
        self._listed.clear()
        self.local.put(key, data)
        self._mirrored[key] = tag
        self._tags[key] = (tag, time.monotonic())
        return tag

    def delete(self, key: str, if_match: str | None = None) -> None:
        """Delete from the remote store, then from the mirror."""
        self.remote.delete(key, if_match=if_match)
        self.reachable = True
        self._listed.clear()
        self._mirrored.pop(key, None)
        self._tags.pop(key, None)
        self.local.delete(key)

    def list(self, prefix: str = "") -> dict[str, str]:
        """List the remote store, or the mirror while it is away; a listing is reused for a second."""
        cached = self._listed.get(prefix)
        if cached and time.monotonic() - cached[0] < 1.0:
            return dict(cached[1])
        if self._resting():
            return self.local.list(prefix)
        try:
            found = self.remote.list(prefix)
        except StoreUnavailable as exc:
            self._away(exc)
            return self.local.list(prefix)
        self.reachable = True
        now = time.monotonic()
        self._listed[prefix] = (now, dict(found))
        for key in [k for k in self._tags if k.startswith(prefix) and k not in found]:
            del self._tags[key]
        for key, tag in found.items():
            self._tags[key] = (tag, now)
        return found


_store: ObjectStore | None = None
_peer_call: Callable[..., httpx.Response] | None = None
_seen: dict[str, str] = {}
_primed = False


def use_peer(call: Callable[..., httpx.Response] | None) -> None:
    """Keep the store on another node, reached with `call`, or go back to the configured one."""
    global _store, _peer_call, _primed
    _peer_call = call
    _store = None
    _primed = False
    _seen.clear()


def _from_url(url: str) -> ObjectStore:
    """Build the store `store_url` names.

    Raises:
        ValueError: For a URL that names no usable store.
    """
    if not url or url == "file":
        return FileStore()
    parts = urlsplit(url)
    if parts.scheme != "s3" or not parts.netloc:
        raise ValueError("SEALSKIN_STORE_URL is `file` or `s3://<bucket>/<prefix>?endpoint=<url>&region=<region>`.")
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    region = query.get("region", "us-east-1")
    endpoint = query.get("endpoint") or f"https://s3.{region}.amazonaws.com"
    if not settings.store_access_key or not settings.store_secret_key:
        raise ValueError("An S3 store needs SEALSKIN_STORE_ACCESS_KEY and SEALSKIN_STORE_SECRET_KEY.")
    return MirroredStore(
        S3Store(endpoint, parts.netloc, parts.path, settings.store_access_key, settings.store_secret_key, region)
    )


def get_store() -> ObjectStore:
    """Return the store this node uses, building it on first use."""
    global _store
    if _store is None:
        _store = MirroredStore(PeerStore(_peer_call)) if _peer_call else _from_url(settings.store_url)
    return _store


def reset() -> None:
    """Forget the built store and what `changed` has seen, so the settings are read again."""
    global _store, _primed
    _store = None
    _primed = False
    _seen.clear()


def is_local() -> bool:
    """Whether the store is this node's own files."""
    return isinstance(get_store(), FileStore)


def is_reachable() -> bool:
    """Whether the store answered its last request."""
    current = get_store()
    return current.reachable if isinstance(current, MirroredStore) else True


def get(key: str) -> tuple[bytes, str] | None:
    """Return an object's bytes and entity tag from the node's store."""
    found = get_store().get(key)
    if found is not None:
        _seen[key] = found[1]
    return found


def put(key: str, data: bytes, if_match: str | None = None, if_absent: bool = False) -> str:
    """Write an object to the node's store and note the write as seen."""
    tag = get_store().put(key, data, if_match=if_match, if_absent=if_absent)
    _seen[key] = tag
    return tag


def delete(key: str, if_match: str | None = None) -> None:
    """Delete an object from the node's store and note it as seen."""
    get_store().delete(key, if_match=if_match)
    _seen.pop(key, None)


def list_keys(prefix: str = "") -> dict[str, str]:
    """Return the entity tags of the objects under `prefix`."""
    return get_store().list(prefix)


def children(prefix: str) -> list[str]:
    """Return the names directly under the `/`-terminated `prefix`, sorted."""
    names = {key[len(prefix) :].split("/", 1)[0] for key in list_keys(prefix)}
    return sorted(name for name in names if name)


def changed() -> set[str]:
    """Return the keys written, replaced, or removed by anyone else since the last call.

    A remote store's changes are copied into the mirror on the way.
    """
    global _primed
    current = get_store()
    listing: dict[str, str] = {}
    for prefix in WATCHED:
        listing.update(current.list(prefix))
    if isinstance(current, MirroredStore) and not current.reachable:
        return set()
    first, _primed = not _primed, True
    differing = {key for key, tag in listing.items() if _seen.get(key) != tag}
    gone = set(_seen) - set(listing)
    if isinstance(current, MirroredStore):
        for key in differing:
            current.get(key)
        for key in gone:
            try:
                current.local.delete(key)
            except (OSError, ValueError):
                pass
    _seen.clear()
    _seen.update(listing)
    return set() if first else differing | gone


def describe() -> dict[str, Any]:
    """Return what the dashboard shows about the store."""
    current = get_store()
    remote = current.remote if isinstance(current, MirroredStore) else current
    kind = {FileStore: "file", S3Store: "s3", PeerStore: "peer"}.get(type(remote), "file")
    detail = f"{remote.endpoint}/{remote.bucket}/{remote.prefix}" if isinstance(remote, S3Store) else ""
    return {"kind": kind, "detail": detail, "reachable": is_reachable()}
