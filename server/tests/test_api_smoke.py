"""End-to-end smoke test through the ASGI app: handshake, JWT, E2EE, install/patch."""

import base64
import json
import os
import time

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from app.settings import settings

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _pem_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return key, priv, pub


class Client:
    """Minimal replica of the browser client's crypto."""

    def __init__(self, http, server_pub_pem, username, user_priv_pem):
        self.http = http
        self.server_pub = serialization.load_pem_public_key(server_pub_pem.encode())
        self.username = username
        self.user_priv = user_priv_pem
        self.session_id = None
        self.aes_key = None

    def handshake(self):
        init = self.http.post("/api/handshake/initiate").json()
        self.server_pub.verify(
            base64.b64decode(init["signature"]),
            base64.b64decode(init["nonce"]),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        self.aes_key = os.urandom(32)
        wrapped = self.server_pub.encrypt(
            self.aes_key,
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        resp = self.http.post(
            "/api/handshake/exchange",
            json={"encrypted_session_key": base64.b64encode(wrapped).decode()},
        )
        assert resp.status_code == 200, resp.text
        self.session_id = resp.json()["session_id"]

    def call(self, method, url, body=None, extra_headers=None):
        now = int(time.time())
        token = jwt.encode(
            {"iat": now, "exp": now + 300, "sub": self.username}, self.user_priv, algorithm="RS256"
        )
        headers = {"X-Session-ID": self.session_id, "Authorization": f"Bearer {token}"}
        headers.update(extra_headers or {})
        content = None
        if body is not None:
            iv = os.urandom(12)
            ct = AESGCM(self.aes_key).encrypt(iv, json.dumps(body).encode(), None)
            content = json.dumps(
                {"iv": base64.b64encode(iv).decode(), "ciphertext": base64.b64encode(ct).decode()}
            )
            headers["Content-Type"] = "application/json"
        resp = self.http.request(method, url, headers=headers, content=content)
        if resp.status_code == 204 or not resp.content:
            return resp.status_code, None
        payload = resp.json()
        if "ciphertext" in payload:
            plain = AESGCM(self.aes_key).decrypt(
                base64.b64decode(payload["iv"]), base64.b64decode(payload["ciphertext"]), None
            )
            return resp.status_code, json.loads(plain)
        return resp.status_code, payload


@pytest.fixture
def api_client(tmp_path, monkeypatch, store_with_firefox):
    _key, server_priv, server_pub = _pem_pair()
    key_path = tmp_path / "server_key.pem"
    key_path.write_text(server_priv)
    monkeypatch.setattr(settings, "server_private_key_path", str(key_path))
    monkeypatch.setattr(settings, "auto_update_apps", False)
    monkeypatch.setattr(settings, "watch_config_files", False)
    monkeypatch.setattr(settings, "ui_path", str(tmp_path / "no-ui"))

    _ukey, user_priv, user_pub = _pem_pair()
    admins = tmp_path / "config" / "keys" / "admins"
    admins.mkdir(parents=True)
    (admins / "tester").write_text(user_pub)
    from app import persistence

    persistence.write_yaml_sync(
        settings.app_stores_path, [{"name": "Test Store", "url": "https://example.invalid/apps.yml"}]
    )

    import app.api as api_module
    from app import security

    security.init_server_keys()
    with TestClient(api_module.api_app) as http:
        client = Client(http, server_pub, "tester", user_priv)
        client.handshake()
        yield client


def test_template_schema_is_public(api_client):
    resp = api_client.http.get("/api/ui/template_schema")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()["settings"]]
    assert "SELKIES_ENCODER" in names and "DOCKER_PRIVILEGED" in names
    assert api_client.http.get("/api/ui/version").json()["bridge"] == 1


def test_status_install_patch_flow(api_client, store_with_firefox):
    status, data = api_client.call("POST", "/api/admin/status", {})
    assert status == 200 and data["is_admin"] is True and data["username"] == "tester"

    status, available = api_client.call(
        "GET", "/api/admin/apps/available?url=https://example.invalid/apps.yml&store_name=Test%20Store"
    )
    assert status == 200 and available[0]["id"] == "firefox"

    install_body = {
        **available[0],
        "id": "inst-1",
        "source": "Test Store",
        "source_app_id": "firefox",
        "home_directories": True,
        "users": ["all"],
        "groups": [],
        "app_template": "Default",
    }
    status, installed = api_client.call("POST", "/api/admin/apps/installed", install_body)
    assert status == 201, installed
    assert installed["name"] == "Firefox"

    status, patched = api_client.call(
        "PATCH", "/api/admin/apps/installed/inst-1", {"name": "Work Firefox", "users": ["tester"]}
    )
    assert status == 200 and patched["name"] == "Work Firefox" and patched["users"] == ["tester"]

    with open(settings.installed_apps_path, encoding="utf-8") as handle:
        on_disk = yaml.safe_load(handle)
    assert on_disk == [
        {
            "id": "inst-1",
            "source": "Test Store",
            "source_app_id": "firefox",
            "app_template": "Default",
            "users": ["tester"],
            "groups": [],
            "auto_update": True,
            "home_directories": True,
            "is_meta_app": False,
            "overrides": {"name": "Work Firefox"},
        }
    ]

    status, apps = api_client.call("POST", "/api/applications", {})
    assert status == 200 and [a["name"] for a in apps] == ["Work Firefox"]

    status, listing = api_client.call("GET", "/api/admin/apps/installed")
    assert status == 200 and listing[0]["provider_config"]["image"].endswith("firefox:latest")

    status, _ = api_client.call("DELETE", "/api/admin/apps/installed/inst-1")
    assert status == 204


def test_unauthenticated_and_bad_session_are_rejected(api_client):
    resp = api_client.http.post("/api/admin/status", headers={"X-Session-ID": "nope"})
    assert resp.status_code in (400, 401)
    assert api_client.http.get("/internal/resolve_session/abc").status_code == 404


def test_idempotency_key_replays_without_reexecuting(api_client, store_with_firefox):
    """A retried POST with the same key is answered once and installs one app."""
    status, available = api_client.call(
        "GET", "/api/admin/apps/available?url=https://example.invalid/apps.yml&store_name=Test%20Store"
    )
    assert status == 200
    install_body = {
        **available[0],
        "id": "inst-idem",
        "source": "Test Store",
        "source_app_id": "firefox",
        "home_directories": True,
        "users": ["all"],
        "groups": [],
        "app_template": "Default",
    }
    key = {"X-Idempotency-Key": "11111111-2222-3333-4444-555555555555"}
    first = api_client.call("POST", "/api/admin/apps/installed", install_body, extra_headers=key)
    second = api_client.call("POST", "/api/admin/apps/installed", install_body, extra_headers=key)
    assert first[0] == 201 and second == first

    status, listing = api_client.call("GET", "/api/admin/apps/installed")
    assert status == 200
    assert [app["id"] for app in listing].count("inst-idem") == 1
