"""The object store: conditional writes on files, S3 request signing, and routing through persistence."""

import httpx
import pytest

from app import persistence, store
from app.settings import settings


def test_file_store_keeps_objects_where_the_settings_say_and_writes_conditionally():
    files = store.FileStore()
    tag = files.put("keys/users/alice", b"one", if_absent=True)
    with open(f"{settings.keys_base_path}/users/alice", "rb") as handle:
        assert handle.read() == b"one"
    with pytest.raises(store.Conflict):
        files.put("keys/users/alice", b"two", if_absent=True)
    newer = files.put("keys/users/alice", b"two", if_match=tag)
    with pytest.raises(store.Conflict):
        files.put("keys/users/alice", b"three", if_match=tag)
    with pytest.raises(store.Conflict):
        files.delete("keys/users/alice", if_match=tag)
    assert files.get("keys/users/alice") == (b"two", newer)
    files.put("installed_apps.yml", b"[]")
    assert set(files.list("")) == {"keys/users/alice", "installed_apps.yml"}
    assert set(files.list("keys/")) == {"keys/users/alice"}
    files.delete("keys/users/alice", if_match=newer)
    assert files.get("keys/users/alice") is None


@pytest.mark.parametrize("key", ["../etc/passwd", "keys/../../x", "sessions.yml", "keys/users/.tmp-x", "keys//x"])
def test_keys_outside_the_shared_objects_are_refused(key):
    with pytest.raises(ValueError):
        store.path_for(key)


def test_paths_map_to_keys_and_back():
    path = f"{settings.groups_base_path}/staff"
    assert store.key_for(path) == "groups/staff"
    assert store.path_for("groups/staff") == path
    assert store.key_for(settings.sessions_db_path) is None


class Bucket:
    """A stand-in S3 service that honors the conditional headers."""

    def __init__(self):
        self.objects = {}
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        key = request.url.path.removeprefix("/bucket/")
        if request.method == "GET" and request.url.path == "/bucket":
            prefix = request.url.params["prefix"]
            items = "".join(
                f"<Contents><Key>{k}</Key><ETag>&quot;{len(v)}&quot;</ETag></Contents>"
                for k, v in sorted(self.objects.items())
                if k.startswith(prefix)
            )
            return httpx.Response(200, text=f"<ListBucketResult><IsTruncated>false</IsTruncated>{items}</ListBucketResult>")
        current = self.objects.get(key)
        tag = f'"{len(current)}"' if current is not None else None
        if request.method == "PUT":
            if request.headers.get("if-none-match") == "*" and current is not None:
                return httpx.Response(412)
            if "if-match" in request.headers and request.headers["if-match"] != tag:
                return httpx.Response(412)
            self.objects[key] = request.content
            return httpx.Response(200, headers={"etag": f'"{len(request.content)}"'})
        if current is None:
            return httpx.Response(404)
        if request.method == "DELETE":
            del self.objects[key]
            return httpx.Response(204)
        return httpx.Response(200, content=current if request.method == "GET" else b"", headers={"etag": tag})


@pytest.fixture
def bucket(monkeypatch):
    service = Bucket()
    remote = store.S3Store(
        "https://s3.example", "bucket", "prod", "AKIDEXAMPLE", "secret", transport=httpx.MockTransport(service.handle)
    )
    monkeypatch.setattr(store, "_store", store.MirroredStore(remote))
    return service


def test_s3_requests_are_signed_and_conditional(bucket):
    tag = store.put("groups/staff", b"gpu: false\n", if_absent=True)
    sent = bucket.requests[-1]
    assert sent.url.path == "/bucket/prod/groups/staff"
    assert sent.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "SignedHeaders=host;if-none-match;x-amz-content-sha256;x-amz-date" in sent.headers["authorization"]
    with pytest.raises(store.Conflict):
        store.put("groups/staff", b"x", if_absent=True)
    with pytest.raises(store.Conflict):
        store.put("groups/staff", b"changed", if_match='"999"')
    assert store.put("groups/staff", b"changed", if_match=tag)
    assert store.list_keys("groups/") == {"groups/staff": '"7"'}


def test_a_remote_store_takes_shared_paths_and_is_mirrored_for_when_it_is_away(bucket, monkeypatch):
    path = f"{settings.groups_base_path}/staff"
    persistence.write_yaml_sync(path, {"gpu": False})
    assert b"gpu: false" in bucket.objects["prod/groups/staff"]
    assert persistence.read_yaml(path) == {"gpu": False}
    assert persistence.list_names(settings.groups_base_path) == ["staff"]
    bucket.objects["prod/groups/staff"] = b"gpu: true\n"
    with pytest.raises(store.Conflict):
        persistence.write_yaml_sync(path, {"gpu": False, "active": True})
    assert persistence.read_yaml(path) == {"gpu": True}
    persistence.write_yaml_sync(path, {"gpu": True, "active": True})

    def down(request):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(store.get_store().remote, "_client", httpx.Client(transport=httpx.MockTransport(down)))
    store.get_store()._tags.clear()
    assert persistence.read_yaml(path) == {"gpu": True, "active": True}
    assert not store.is_reachable()
    with pytest.raises(store.StoreUnavailable):
        persistence.write_yaml_sync(path, {"gpu": False})


def test_changed_reports_what_another_node_wrote_and_not_this_one(bucket):
    assert store.changed() == set()
    store.put("groups/mine", b"a: 1\n")
    bucket.objects["prod/groups/theirs"] = b"b: 2\n"
    store.get_store()._listed.clear()
    assert store.changed() == {"groups/theirs"}
    del bucket.objects["prod/groups/theirs"]
    store.get_store()._listed.clear()
    assert store.changed() == {"groups/theirs"}
    assert store.changed() == set()
