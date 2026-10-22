"""Application keys: made on the web, used by the companion apps, bound by the settings, and the built-in administrator's way in."""

# ruff: noqa: F811 - the `provider` fixture is imported from the sign-in tests and taken by name

import json
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import application_keys, persistence, security, sso, user_manager
from app.settings import settings
from tests.conftest import PROXY_SECRET, WEB
from tests.test_api_smoke import _pem_pair
from tests.test_sso import (
    _sign_in_oidc,
    provider,  # noqa: F401 - the stand-in identity provider, a fixture
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: A companion app's request: through the proxy, with no browser account of its origin.
APP = {"X-SealSkin-Secret": PROXY_SECRET}


@pytest.fixture
def http(tmp_path, monkeypatch, store_with_firefox, provider):
    _key, server_priv, _server_pub = _pem_pair()
    key_path = tmp_path / "server_key.pem"
    key_path.write_text(server_priv)
    monkeypatch.setattr(settings, "server_private_key_path", str(key_path))
    monkeypatch.setattr(settings, "auto_update_apps", False)
    monkeypatch.setattr(settings, "watch_config_files", False)
    monkeypatch.setattr(settings, "ui_path", str(tmp_path / "no-ui"))
    _ukey, _upriv, admin_pub = _pem_pair()
    admins = tmp_path / "config" / "keys" / "admins"
    admins.mkdir(parents=True)
    (admins / "tester").write_text(admin_pub)
    import app.api as api_module

    security.init_server_keys()
    application_keys._FAILURES.clear()
    with TestClient(api_module.api_app) as client:
        yield client


def _alice(http, provider, groups=("users",)):
    """Sign alice in through the provider, in a `users` group that exists, so nothing holds her."""
    if "users" not in user_manager.GROUP_DATA:
        user_manager.write_group_file("users", {})
    return _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice", "groups": list(groups)})


def _as_app(http, key, client="chrome", method="POST", url="/api/admin/status", body=None):
    headers = {**APP, "Authorization": f"Bearer {key}", application_keys.CLIENT_HEADER: client}
    return http.request(method, url, json=body if body is not None else {}, headers=headers)


def test_the_first_start_gives_the_one_administrator_a_key_file_and_the_root_key(tmp_path, monkeypatch, store_with_firefox):
    # Mirror the shipped layout, where the key file lands three levels above the keys directory, in `/config`.
    monkeypatch.setattr(settings, "keys_base_path", str(tmp_path / "config" / ".config" / "sealskin" / "keys"))
    _key, server_priv, _server_pub = _pem_pair()
    key_path = tmp_path / "server_key.pem"
    key_path.write_text(server_priv)
    monkeypatch.setattr(settings, "server_private_key_path", str(key_path))
    monkeypatch.setattr(settings, "auto_update_apps", False)
    monkeypatch.setattr(settings, "watch_config_files", False)
    monkeypatch.setattr(settings, "ui_path", str(tmp_path / "no-ui"))
    import app.api as api_module

    security.init_server_keys()
    application_keys._FAILURES.clear()
    with TestClient(api_module.api_app) as http:
        key_file = json.loads((tmp_path / "config" / "admin.json").read_text())
        assert key_file["username"] == user_manager.ADMIN == "admin"
        assert user_manager.get_user("admin")["is_admin"] is True and user_manager.get_user("admin")["public_key"]
        # The key file's token, as the extension signs it, over the proxy and so with no session named.
        now = int(time.time())
        token = jwt.encode({"iat": now, "exp": now + 300, "sub": "admin"}, key_file["private_key"], algorithm="RS256")
        by_file = http.post("/api/admin/status", json={}, headers={**WEB, "Authorization": f"Bearer {token}"})
        assert by_file.status_code == 200 and by_file.json()["username"] == "admin" and by_file.json()["is_admin"] is True
        # The root key, on the web and as a bearer in the apps, is the same account's.
        root_key = open(settings.root_key_path, encoding="utf-8").read().strip()
        signed_in = http.post("/api/auth/key", json={"key": root_key}, headers=WEB)
        assert signed_in.status_code == 200 and signed_in.json()["username"] == "admin"
        assert _as_app(http, root_key).json()["username"] == "admin"
        assert [k["name"] for k in application_keys.list_for("admin")] == ["first start"]
        # The one account stays: neither a provider nor the dashboard may take or drop the name.
        with pytest.raises(ValueError):
            user_manager.ensure_user("admin", "oidc", "oidc https://idp sub-9")
        with pytest.raises(ValueError):
            user_manager.delete_admin("admin")


def test_the_first_start_writes_root_a_key_the_web_takes_and_the_store_keeps_hashed(http):
    key = open(settings.root_key_path, encoding="utf-8").read().strip()
    assert key.startswith("ssk_") and application_keys.parse(key)
    assert application_keys.parse(key)[1] not in persistence.read_bytes(application_keys._path()).decode()
    assert http.post("/api/auth/key", json={"key": key}).status_code == 403
    signed_in = http.post("/api/auth/key", json={"key": key}, headers=WEB)
    assert signed_in.status_code == 200 and signed_in.json()["username"] == "admin" and signed_in.json()["via"] == "root"
    cookie = {"Cookie": f"{sso.SESSION_COOKIE}={signed_in.headers['set-cookie'].split('=', 1)[1].split(';')[0]}"}
    status = http.post("/api/admin/status", json={}, headers={**WEB, **cookie})
    assert status.status_code == 200 and status.json()["is_admin"] is True
    # Root's key is for the web alone through this route, and also serves the apps like any key.
    assert _as_app(http, key).status_code == 200


def test_wrong_keys_are_counted_per_address_and_the_environment_key_is_matched_whole(http, monkeypatch):
    for _ in range(5):
        refused = http.post("/api/auth/key", json={"key": "ssk_nope_nothing"}, headers={**WEB, "X-SealSkin-Client": "10.0.0.9"})
        assert refused.status_code == 403 and refused.json()["detail"] == "refused"
    locked = http.post("/api/auth/key", json={"key": "whatever"}, headers={**WEB, "X-SealSkin-Client": "10.0.0.9"})
    assert locked.status_code == 429 and locked.json()["detail"] == "locked"
    monkeypatch.setattr(settings, "root_key", "from-a-secret-manager")
    application_keys.ensure_root_key()
    assert [k["name"] for k in application_keys.list_for("admin")] == ["environment", "first start"]
    assert http.post("/api/auth/key", json={"key": "from-a-secret-manager"}, headers=WEB).status_code == 200
    # The environment's key signs the apps in too, on the companion lane as anywhere.
    lane = {**APP, "X-SealSkin-Lane": "companion", "Authorization": "Bearer from-a-secret-manager", application_keys.CLIENT_HEADER: "other"}
    assert http.post("/api/admin/status", json={}, headers=lane).json()["username"] == "admin"
    assert _as_app(http, "from-a-secret-manager").json()["username"] == "admin"
    assert _as_app(http, "nobody-s-key").json()["detail"] == "unknown"
    monkeypatch.setattr(settings, "root_key", "")
    application_keys.ensure_root_key()
    assert [k["name"] for k in application_keys.list_for("admin")] == ["first start"]


def test_a_user_makes_a_key_on_the_web_and_the_app_signs_in_with_it(http, provider):
    alice = _alice(http, provider)
    status, listing = alice.call("GET", "/api/keys")
    assert status == 200 and listing["keys"] == [] and listing["policy"]["allowed"] is True
    assert listing["policy"]["clients"] == ["android", "chrome", "firefox", "ios", "other"]
    assert listing["policy"]["web_sign_in"]["at"] <= time.time() < listing["policy"]["web_sign_in"]["until"]
    status, made = alice.call("POST", "/api/keys", {"name": "My phone"})
    assert status == 201 and made["key"].startswith("ssk_") and made["record"]["name"] == "My phone"
    assert made["record"]["expires"] is None and made["record"]["state"] == "active"
    # The key carries the server's address for the app to read; the server takes it with or without.
    assert made["key"].count("@") == 1 and made["key"].split("@")[1]
    assert _as_app(http, made["key"], client="android").status_code == 200
    key = made["key"].split("@")[0]
    # The app: plain lane, bearer, no browser account of the origin.
    answer = _as_app(http, key, client="android")
    assert answer.status_code == 200, answer.text
    assert answer.json()["username"] == "alice" and answer.json()["via"] == "app_key" and answer.json()["is_admin"] is False
    status, listing = alice.call("GET", "/api/keys")
    assert listing["keys"][0]["client"] == "android" and listing["keys"][0]["last_used"] > 0
    # A key cannot make keys, and a wrong secret under a known id is nobody's.
    assert _as_app(http, key, method="GET", url="/api/keys").status_code == 403
    key_id, _secret = application_keys.parse(key)
    refused = _as_app(http, f"ssk_{key_id}_wrong")
    assert refused.status_code == 401 and refused.json()["detail"] == "unknown"
    # A key on the API port, past the proxy, is refused before any handler: the plain lane runs through Caddy alone.
    bare = http.post("/api/admin/status", json={}, headers={"Authorization": f"Bearer {key}"})
    assert bare.status_code == 400
    # Revoked, it is gone.
    status, _ = alice.call("DELETE", f"/api/keys/{key_id}")
    assert status == 204
    assert _as_app(http, key).json()["detail"] == "unknown"


def test_the_companion_lane_takes_keys_alone(http, provider):
    alice = _alice(http, provider)
    status, made = alice.call("POST", "/api/keys", {"name": "Phone"})
    lane = {"X-SealSkin-Lane": "companion"}
    # The cookie counts for nothing there, nor does a proxy's header.
    status, body = alice.call("POST", "/api/admin/status", {}, extra_headers=lane)
    assert status == 401 and "application keys alone" in body["detail"]
    answer = http.post("/api/admin/status", json={}, headers={**APP, **lane, "Authorization": f"Bearer {made['key']}", application_keys.CLIENT_HEADER: "ios"})
    assert answer.status_code == 200 and answer.json()["via"] == "app_key"
    # The lane is the proxy's to mark: a client saying so itself, past the proxy, is just a client.
    bare = http.post("/api/admin/status", json={}, headers={"X-SealSkin-Lane": "companion", "Authorization": f"Bearer {made['key']}"})
    assert bare.status_code == 400


def test_the_settings_bind_keys_lifetime_clients_and_whether_there_are_any(http, provider):
    alice = _alice(http, provider)
    user_manager.write_group_file("users", {"application_key_days": 7, "application_key_clients": ["ios", "android"]})
    status, listing = alice.call("GET", "/api/keys")
    assert listing["policy"]["max_days"] == 7 and listing["policy"]["clients"] == ["android", "ios"]
    status, refused = alice.call("POST", "/api/keys", {"name": "Too long", "days": 30})
    assert status == 400 and refused["detail"] == "days"
    status, made = alice.call("POST", "/api/keys", {"name": "Tablet"})
    assert status == 201 and 6.9 * 86400 < made["record"]["expires"] - time.time() <= 7 * 86400
    key = made["key"]
    assert _as_app(http, key, client="ios").status_code == 200
    refused = _as_app(http, key, client="chrome")
    assert refused.status_code == 401 and refused.json()["detail"] == "client"
    refused = _as_app(http, key, client="curl/8")
    assert refused.json()["detail"] == "client"
    # Expired by the clock.
    application_keys._KEYS[made["record"]["id"]]["expires"] = time.time() - 1
    assert _as_app(http, key, client="ios").json()["detail"] == "expired"
    application_keys._KEYS[made["record"]["id"]]["expires"] = None
    # Denied keys altogether: none are used, none are made.
    user_manager.write_group_file("users", {"application_keys": False})
    assert _as_app(http, key, client="ios").json()["detail"] == "disabled"
    status, refused = alice.call("POST", "/api/keys", {"name": "Another"})
    assert status == 403 and refused["detail"] == "disabled"


def test_dormant_keys_wait_for_a_web_sign_in_and_wake_with_it(http, provider):
    alice = _alice(http, provider)
    status, made = alice.call("POST", "/api/keys", {"name": "Laptop"})
    key = made["key"]
    user_manager.write_group_file("users", {"application_key_dormant_hours": 24})
    assert _as_app(http, key).status_code == 200
    # A sign-in a day and a bit ago: the key sleeps, and the listing says so.
    stamp = user_manager.web_sign_in_of("alice")
    user_manager._write_web_sign_in("alice", {"at": time.time() - 25 * 3600, "until": stamp["until"]})
    refused = _as_app(http, key)
    assert refused.status_code == 401 and refused.json()["detail"] == "dormant"
    status, listing = alice.call("GET", "/api/keys")
    assert listing["keys"][0]["state"] == "dormant"
    # The browser still signed in wakes the keys on request, as the app's "sign in on the web" lands there.
    status, listing = alice.call("POST", "/api/keys/wake", {})
    assert status == 200 and listing["keys"][0]["state"] == "active"
    assert _as_app(http, key).status_code == 200
    # Following the sign-in: the key lives while the provider's does, and sleeps when it ends.
    user_manager.write_group_file("users", {"application_key_follow_sign_in": True})
    assert _as_app(http, key).status_code == 200
    user_manager.end_web_sign_in("alice")
    assert _as_app(http, key).json()["detail"] == "dormant"
    # A fresh sign-in through the provider wakes them again.
    _alice(http, provider)
    assert _as_app(http, key).status_code == 200


def test_an_administrator_sees_and_revokes_a_users_keys_and_a_deleted_user_leaves_none(http, provider):
    alice = _alice(http, provider)
    status, made = alice.call("POST", "/api/keys", {"name": "Phone"})
    root_key = open(settings.root_key_path, encoding="utf-8").read().strip()
    signed_in = http.post("/api/auth/key", json={"key": root_key}, headers=WEB)
    root = {**WEB, "Cookie": f"{sso.SESSION_COOKIE}={signed_in.headers['set-cookie'].split('=', 1)[1].split(';')[0]}"}
    listed = http.get("/api/admin/users/alice/keys", headers=root)
    assert listed.status_code == 200 and [k["name"] for k in listed.json()] == ["Phone"]
    assert http.delete(f"/api/admin/users/alice/keys/{made['record']['id']}", headers=root).status_code == 204
    assert http.get("/api/admin/users/alice/keys", headers=root).json() == []
    status, made = alice.call("POST", "/api/keys", {"name": "Phone again"})
    assert http.delete("/api/admin/users/alice", headers=root).status_code == 204
    assert application_keys.list_for("alice") == []
    assert _as_app(http, made["key"]).json()["detail"] == "unknown"


def test_without_key_files_the_dashboard_makes_sign_in_only_users_and_no_administrators(http, monkeypatch):
    monkeypatch.setattr(settings, "legacy_auth", False)
    root_key = open(settings.root_key_path, encoding="utf-8").read().strip()
    root = {**APP, "Authorization": f"Bearer {root_key}", application_keys.CLIENT_HEADER: "other"}
    assert http.post("/api/admin/status", json={}, headers=root).json()["key_files"] is False
    made = http.post("/api/admin/users", json={"username": "carol", "public_key": None, "settings": {}}, headers=root)
    assert made.status_code == 201 and made.json()["private_key"] is None and made.json()["user"]["public_key"] == ""
    refused = http.post("/api/admin/admins", json={"username": "boss", "public_key": None}, headers=root)
    assert refused.status_code == 400 and "Key-file clients are off" in refused.json()["detail"]
    monkeypatch.setattr(settings, "legacy_auth", True)
    assert http.post("/api/admin/status", json={}, headers=root).json()["key_files"] is True


def test_an_administrators_keys_are_bound_by_the_rules_too(http, provider):
    boss = _sign_in_oidc(http, provider, {"sub": "b-1", "preferred_username": "boss", "groups": ["admins"]})
    user_manager.write_group_file("admins", {"application_key_days": 1})
    status, listing = boss.call("GET", "/api/keys")
    assert status == 200 and listing["policy"]["max_days"] == 1
    status, made = boss.call("POST", "/api/keys", {"name": "Boss phone"})
    assert status == 201 and made["record"]["expires"] - time.time() <= 86400
    answer = _as_app(http, made["key"], client="firefox")
    assert answer.status_code == 200 and answer.json()["is_admin"] is True and answer.json()["via"] == "app_key"


def test_the_app_names_itself_apart_from_the_address_the_proxy_marks(http, provider):
    alice = _alice(http, provider)
    user_manager.write_group_file("users", {"application_key_clients": ["android"]})
    status, made = alice.call("POST", "/api/keys", {"name": "Phone"})
    assert status == 201
    # Caddy writes the browser's address in `X-SealSkin-Client`; the app's own name rides `X-SealSkin-App`.
    marked = {**APP, "Authorization": f"Bearer {made['key']}", "X-SealSkin-Client": "203.0.113.9", "X-SealSkin-App": "android"}
    answer = http.post("/api/admin/status", json={}, headers=marked)
    assert answer.status_code == 200, answer.text
    assert application_keys.CLIENT_HEADER == "X-SealSkin-App"
    assert application_keys._KEYS[made["record"]["id"]]["client"] == "android"
    unnamed = http.post("/api/admin/status", json={}, headers={**APP, "Authorization": f"Bearer {made['key']}", "X-SealSkin-Client": "203.0.113.9"})
    assert unnamed.status_code == 401 and unnamed.json()["detail"] == "client"


def test_wrong_whole_bearers_lock_their_address_out_and_no_other(http, monkeypatch):
    monkeypatch.setattr(settings, "root_key", "from-a-secret-manager")
    application_keys.ensure_root_key()
    guesser = {**APP, "X-SealSkin-Client": "203.0.113.5", application_keys.CLIENT_HEADER: "other"}
    for _ in range(5):
        refused = http.post("/api/admin/status", json={}, headers={**guesser, "Authorization": "Bearer not-it"})
        assert refused.status_code == 401 and refused.json()["detail"] == "unknown"
    locked = http.post("/api/admin/status", json={}, headers={**guesser, "Authorization": "Bearer from-a-secret-manager"})
    assert locked.status_code == 429 and locked.json()["detail"] == "locked"
    other = {**guesser, "X-SealSkin-Client": "203.0.113.6", "Authorization": "Bearer from-a-secret-manager"}
    assert http.post("/api/admin/status", json={}, headers=other).json()["username"] == "admin"
    # An issued key is found by its unguessable id, so it is neither counted nor locked out.
    root_key = open(settings.root_key_path, encoding="utf-8").read().strip()
    assert http.post("/api/admin/status", json={}, headers={**guesser, "Authorization": f"Bearer {root_key}"}).status_code == 200
    assert http.post("/api/admin/status", json={}, headers={**guesser, "Authorization": "Bearer ssk_nobody_nothing"}).json()["detail"] == "unknown"
