"""Identity provider sign-in against a stand-in provider: OpenID Connect, SAML, and the web sign-ins they start."""

import base64
import copy
import datetime
import hashlib
import os
import time
import uuid
import zlib
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from lxml import etree
from signxml import XMLSigner

from app import sso, user_manager
from app.settings import settings
from tests.conftest import WEB
from tests.test_api_smoke import Client, _pem_pair

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

ISSUER = "https://idp.example/realms/test"
SAML_ENTITY = "https://idp.example/saml"
BASE = "http://testserver"
ACS = f"{BASE}/api/auth/saml/acs"
SP_ENTITY = f"{BASE}/api/auth/saml/metadata"
SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML = "urn:oasis:names:tc:SAML:2.0:assertion"


def _certificate(key):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.example")])
    now = datetime.datetime.now(datetime.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )


class Provider:
    """A stand-in identity provider serving discovery, keys, tokens, and SAML metadata."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.key_pem = self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        self.cert = _certificate(self.key)
        self.cert_pem = self.cert.public_bytes(serialization.Encoding.PEM)
        self.codes = {}
        self.refresh = lambda form: httpx.Response(200, json={"refresh_token": "r2", "expires_in": 300})
        self.token_calls = []
        #: What the UserInfo endpoint answers; the provider publishes none while this is None.
        self.userinfo = None

    def id_token(self, claims, key=None, kid="k1"):
        now = int(time.time())
        body = {"iss": ISSUER, "aud": "sealskin", "iat": now, "exp": now + 300, **claims}
        return jwt.encode(body, key or self.key, algorithm="RS256", headers={"kid": kid})

    def issue_code(self, claims, challenge, **tokens):
        code = uuid.uuid4().hex
        self.codes[code] = (challenge, {"id_token": self.id_token(claims), "refresh_token": "r1", **tokens})
        return code

    def metadata(self):
        cert = base64.b64encode(self.cert.public_bytes(serialization.Encoding.DER)).decode()
        return (
            f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
            f'xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID="{SAML_ENTITY}">'
            '<md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
            f'<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{cert}'
            "</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>"
            '<md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" '
            'Location="https://idp.example/saml/slo"/>'
            '<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" '
            'Location="https://idp.example/saml/sso"/>'
            "</md:IDPSSODescriptor></md:EntityDescriptor>"
        )

    def handle(self, request):
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/auth",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/certs",
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                    **({"userinfo_endpoint": f"{ISSUER}/userinfo"} if self.userinfo is not None else {}),
                },
            )
        if url == f"{ISSUER}/userinfo":
            assert request.headers["authorization"] == "Bearer at-1"
            return httpx.Response(200, json=self.userinfo)
        if url == f"{ISSUER}/certs":
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]})
        if url == f"{ISSUER}/token":
            form = parse_qs(request.content.decode())
            self.token_calls.append(form)
            assert request.headers["authorization"] == "Basic " + base64.b64encode(b"sealskin:s3cret").decode()
            if form["grant_type"] == ["refresh_token"]:
                return self.refresh(form)
            challenge, tokens = self.codes.pop(form["code"][0], (None, None))
            verifier = form["code_verifier"][0].encode()
            if tokens is None or challenge != sso._b64url(hashlib.sha256(verifier).digest()):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json=tokens)
        if url == f"{SAML_ENTITY}/metadata":
            return httpx.Response(200, text=self.metadata())
        return httpx.Response(404)


@pytest.fixture
def provider(monkeypatch):
    idp = Provider()
    monkeypatch.setattr(sso, "_transport", httpx.MockTransport(idp.handle))
    for name, value in {
        "oidc_issuer": ISSUER,
        "oidc_client_id": "sealskin",
        "oidc_client_secret": "s3cret",
        "saml_metadata_url": f"{SAML_ENTITY}/metadata",
        "sso_admin_group": "admins",
        "sso_groups_claim": "groups",
        "sso_username_claim": "",
    }.items():
        monkeypatch.setattr(settings, name, value)
    for table in (sso._KEYS, sso._FLOWS, sso._TICKETS, sso._GRANTS, sso._REPLAYS, sso._CACHE):
        table.clear()
    return idp


@pytest.fixture
def http(tmp_path, monkeypatch, store_with_firefox, provider):
    _key, server_priv, server_pub = _pem_pair()
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
    from app import security

    security.init_server_keys()
    with TestClient(api_module.api_app) as client:
        client.server_pub = server_pub
        yield client


def _oidc_callback(http, provider, claims, **tokens):
    start = http.get("/api/auth/oidc/login", follow_redirects=False)
    assert start.status_code == 303
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = provider.issue_code({"nonce": query["nonce"][0], **claims}, query["code_challenge"][0], **tokens)
    return http.get(f"/api/auth/oidc/callback?state={query['state'][0]}&code={code}", follow_redirects=False)


def _fragment(response):
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith("/ui/#"), location
    key, _, value = location[len("/ui/#") :].partition("=")
    return key, value


def _register(http, grant):
    response = http.post("/api/auth/register", json={"grant": grant}, headers=WEB)
    cookie = response.headers.get("set-cookie", "")
    token = cookie.split(f"{sso.SESSION_COOKIE}=", 1)[1].split(";", 1)[0] if sso.SESSION_COOKIE in cookie else None
    return response, token


class SignedIn:
    """The web app's requests: plain JSON through the proxy, with the sign-in's cookie."""

    def __init__(self, http, registration, token):
        self.http = http
        self.username = registration["username"]
        self.token = token
        self.kid = sso.session_id(token)

    def call(self, method, url, body=None, extra_headers=None):
        headers = {**WEB, "Cookie": f"{sso.SESSION_COOKIE}={self.token}", **(extra_headers or {})}
        response = self.http.request(method, url, json=body, headers=headers)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None


def _sign_in_oidc(http, provider, claims, **tokens):
    kind, grant = _fragment(_oidc_callback(http, provider, claims, **tokens))
    assert kind == "sso", grant
    response, priv = _register(http, grant)
    assert response.status_code == 200, response.text
    return SignedIn(http, response.json(), priv)


def test_config_names_the_configured_sign_ins(http, monkeypatch):
    offered = {"proxy": False, "root": True, "key": True}
    assert http.get("/api/auth/config").json() == {"oidc": True, "saml": True, **offered}
    monkeypatch.setattr(settings, "saml_metadata_url", "")
    assert http.get("/api/auth/config").json() == {"oidc": True, "saml": False, **offered}


def test_oidc_login_asks_for_a_fresh_authentication_with_pkce(http, monkeypatch):
    monkeypatch.setattr(settings, "sso_force_login", True)
    start = http.get("/api/auth/oidc/login", follow_redirects=False)
    target = urlparse(start.headers["location"])
    query = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{ISSUER}/auth"
    assert query["prompt"] == ["login"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [f"{BASE}/api/auth/oidc/callback"] and query["scope"] == ["openid profile email"]
    assert "sealskin_sso=" in start.headers["set-cookie"] and "HttpOnly" in start.headers["set-cookie"]


def test_oidc_sign_in_creates_the_user_and_starts_a_web_sign_in(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice", "groups": ["users"]})
    status, data = client.call("POST", "/api/admin/status", {})
    assert status == 200 and data["username"] == "alice" and data["is_admin"] is False
    assert user_manager.get_user("alice")["public_key"] == ""
    assert os.stat(settings.sso_keys_path).st_mode & 0o777 == 0o600


def test_the_user_and_groups_an_id_token_leaves_out_come_from_userinfo(http, provider):
    provider.userinfo = {"sub": "b-1", "preferred_username": "bob", "groups": ["admins"]}
    client = _sign_in_oidc(http, provider, {"sub": "b-1"}, access_token="at-1")
    status, data = client.call("POST", "/api/admin/status", {})
    assert status == 200 and data["username"] == "bob" and data["is_admin"] is True


def test_userinfo_for_another_account_signs_nobody_in(http, provider):
    provider.userinfo = {"sub": "someone-else", "preferred_username": "bob"}
    kind, code = _fragment(_oidc_callback(http, provider, {"sub": "b-1"}, access_token="at-1"))
    assert (kind, code) == ("sso-error", "failed")


def test_an_id_token_that_names_the_user_is_not_checked_against_userinfo(http, provider):
    provider.userinfo = {"sub": "a-1", "preferred_username": "mallory", "groups": ["admins"]}
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice", "groups": []}, access_token="at-1")
    status, data = client.call("POST", "/api/admin/status", {})
    assert status == 200 and data["username"] == "alice" and data["is_admin"] is False


def test_a_key_file_token_never_reaches_a_user_with_no_key(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    plain = Client(http, http.server_pub, "alice", _pem_pair()[1])
    plain.handshake()
    assert plain.call("POST", "/api/admin/status", {})[0] == 401
    assert client.call("POST", "/api/admin/status", {})[0] == 200


def test_the_admin_group_grants_administration_and_a_refresh_takes_it_back(http, provider, monkeypatch):
    client = _sign_in_oidc(http, provider, {"sub": "b-1", "preferred_username": "bob", "groups": ["/admins"]})
    status, data = client.call("POST", "/api/admin/status", {})
    assert status == 200 and data["is_admin"] is True
    monkeypatch.setattr(sso, "CHECK_SECONDS", 0)
    provider.refresh = lambda form: httpx.Response(
        200, json={"refresh_token": "r2", "id_token": provider.id_token({"sub": "b-1", "groups": ["users"]})}
    )
    status, data = client.call("POST", "/api/admin/status", {})
    assert status == 200 and data["is_admin"] is False
    assert provider.token_calls[-1]["refresh_token"] == ["r1"]


def test_a_sign_in_the_provider_ends_is_refused(http, provider, monkeypatch):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    monkeypatch.setattr(sso, "CHECK_SECONDS", 0)
    provider.refresh = lambda form: httpx.Response(400, json={"error": "invalid_grant"})
    status, data = client.call("POST", "/api/admin/status", {})
    assert status == 401 and "ended" in data["detail"]
    assert client.kid not in sso._KEYS


def test_an_unreachable_provider_keeps_the_sign_in(http, provider, monkeypatch):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    monkeypatch.setattr(sso, "CHECK_SECONDS", 0)

    def down(form):
        raise httpx.ConnectError("unreachable")

    provider.refresh = down
    assert client.call("POST", "/api/admin/status", {})[0] == 200


def test_sign_out_ends_the_sign_in(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    assert client.call("POST", "/api/auth/signout", {})[0] == 204
    assert client.call("POST", "/api/admin/status", {})[0] == 401


def test_sign_out_takes_the_cookie_back(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    cookie = {"Cookie": f"{sso.SESSION_COOKIE}={client.token}"}
    # Another site's page ends nobody's sign-in.
    assert http.post("/api/auth/signout", headers={**WEB, **cookie, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.call("POST", "/api/admin/status", {})[0] == 200
    answer = http.post("/api/auth/signout", headers={**WEB, **cookie})
    assert answer.status_code == 204
    cleared = answer.headers["set-cookie"]
    assert cleared.startswith(f'{sso.SESSION_COOKIE}=""') and "Max-Age=0" in cleared and "Secure" in cleared
    assert http.post("/api/auth/signout", headers=WEB).status_code == 204


@pytest.mark.parametrize(
    "claims,token_key",
    [
        ({"aud": "someone-else"}, None),
        ({"exp": int(time.time()) - 600, "iat": int(time.time()) - 900}, None),
        ({"iss": "https://evil.example"}, None),
        ({}, "forged"),
    ],
)
def test_an_id_token_that_does_not_verify_is_refused(http, provider, claims, token_key):
    start = http.get("/api/auth/oidc/login", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    body = {"sub": "a-1", "preferred_username": "alice", "nonce": query["nonce"][0], **claims}
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if token_key else None
    code = uuid.uuid4().hex
    provider.codes[code] = (query["code_challenge"][0], {"id_token": provider.id_token(body, key=key)})
    response = http.get(f"/api/auth/oidc/callback?state={query['state'][0]}&code={code}", follow_redirects=False)
    assert _fragment(response) == ("sso-error", "failed")


def test_a_nonce_from_another_flow_is_refused(http, provider):
    response = _oidc_callback(http, provider, {"sub": "a-1", "preferred_username": "alice", "nonce": "other"})
    assert _fragment(response) == ("sso-error", "failed")


def test_a_flow_finishes_once_and_only_in_its_browser(http, provider):
    start = http.get("/api/auth/oidc/login", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = provider.issue_code({"sub": "a-1", "preferred_username": "alice", "nonce": query["nonce"][0]}, query["code_challenge"][0])
    callback = f"/api/auth/oidc/callback?state={query['state'][0]}&code={code}"
    with TestClient(http.app) as stranger:
        assert _fragment(stranger.get(callback, follow_redirects=False)) == ("sso-error", "browser")
    assert _fragment(http.get(callback, follow_redirects=False)) == ("sso-error", "expired")
    assert _fragment(http.get("/api/auth/oidc/callback?state=unknown&code=x", follow_redirects=False)) == (
        "sso-error",
        "expired",
    )


def test_a_grant_starts_one_sign_in(http, provider):
    kind, grant = _fragment(_oidc_callback(http, provider, {"sub": "a-1", "preferred_username": "alice"}))
    assert _register(http, grant)[0].status_code == 200
    response = _register(http, grant)[0]
    assert response.status_code == 400 and response.json()["detail"] == "expired"


def test_a_grant_starts_a_sign_in_only_in_the_browser_that_signed_in(http, provider):
    kind, grant = _fragment(_oidc_callback(http, provider, {"sub": "a-1", "preferred_username": "alice"}))
    with TestClient(http.app) as victim:
        response = _register(victim, grant)[0]
        assert response.status_code == 400 and response.json()["detail"] == "browser"
    assert _register(http, grant)[0].json()["detail"] == "expired"


@pytest.mark.parametrize(
    "claims,code",
    [
        ({"sub": "d-1", "preferred_username": "dave.smith@example.com"}, "invalidUsername"),
        ({"sub": "d-1"}, "invalidUsername"),
        ({"sub": "t-1", "preferred_username": "tester"}, "adminName"),
    ],
)
def test_a_name_sealskin_cannot_take_is_refused(http, provider, claims, code):
    assert _fragment(_oidc_callback(http, provider, claims)) == ("sso-error", code)


def test_a_user_stays_bound_to_the_account_that_first_signed_in_as_it(http, provider):
    _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    response = _oidc_callback(http, provider, {"sub": "a-2", "preferred_username": "alice"})
    assert _fragment(response) == ("sso-error", "otherIdentity")
    assert _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"}).kid


def test_each_protocol_binds_the_user_to_its_own_subject(http, provider):
    _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    request, relay = _saml_start(http)
    saml_response = _response(provider, request.get("ID"), _assertion(request.get("ID")))
    assert _finish(http, _post(http, relay, saml_response))[0] == "sso"
    response = _oidc_callback(http, provider, {"sub": "a-2", "preferred_username": "alice"})
    assert _fragment(response) == ("sso-error", "otherIdentity")


def test_a_sign_in_past_its_end_is_refused(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    sso._KEYS[client.kid]["expires"] = time.time() - 1
    assert client.call("POST", "/api/admin/status", {})[0] == 401


def test_pending_flows_are_capped(http, provider, monkeypatch):
    monkeypatch.setattr(sso, "MAX_PENDING", 3)
    for _ in range(5):
        http.get("/api/auth/oidc/login", follow_redirects=False)
    assert len(sso._FLOWS) == 3


def test_registrations_survive_a_restart(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    sso._KEYS.clear()
    sso.load()
    assert client.call("POST", "/api/admin/status", {})[0] == 200


def _logout_token(provider, claims, key=None):
    now = int(time.time())
    body = {"iss": ISSUER, "aud": "sealskin", "iat": now, "jti": uuid.uuid4().hex, "events": {sso.BACKCHANNEL_LOGOUT_EVENT: {}}}
    return jwt.encode({**body, **claims}, key or provider.key, algorithm="RS256", headers={"kid": "k1"})


def test_a_backchannel_logout_ends_the_session_or_the_subject_it_names(http, provider):
    first = _sign_in_oidc(http, provider, {"sub": "a-1", "sid": "s-1", "preferred_username": "alice"})
    second = _sign_in_oidc(http, provider, {"sub": "a-1", "sid": "s-2", "preferred_username": "alice"})
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for claims, key in (
        ({"sid": "s-1", "nonce": "n"}, None),
        ({"sid": "s-1", "aud": "someone-else"}, None),
        ({"sid": "s-1", "events": {}}, None),
        ({}, None),
        ({"sid": "s-1"}, forged),
    ):
        response = http.post("/api/auth/oidc/backchannel-logout", data={"logout_token": _logout_token(provider, claims, key)})
        assert response.status_code == 400, claims
    token = _logout_token(provider, {"sid": "s-1", "sub": "a-1"})
    assert http.post("/api/auth/oidc/backchannel-logout", data={"logout_token": token}).status_code == 200
    assert first.call("POST", "/api/admin/status", {})[0] == 401
    assert second.call("POST", "/api/admin/status", {})[0] == 200
    assert http.post("/api/auth/oidc/backchannel-logout", data={"logout_token": token}).status_code == 400
    token = _logout_token(provider, {"sub": "a-1"})
    assert http.post("/api/auth/oidc/backchannel-logout", data={"logout_token": token}).status_code == 200
    assert second.call("POST", "/api/admin/status", {})[0] == 401


def test_a_backchannel_logout_reaches_a_sign_in_whose_id_token_named_no_session(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "preferred_username": "alice"})
    only_sid = _logout_token(provider, {"sid": "s-9"})
    assert http.post("/api/auth/oidc/backchannel-logout", data={"logout_token": only_sid}).status_code == 200
    assert client.call("POST", "/api/admin/status", {})[0] == 200
    token = _logout_token(provider, {"sid": "s-9", "sub": "a-1"})
    assert http.post("/api/auth/oidc/backchannel-logout", data={"logout_token": token}).status_code == 200
    assert client.call("POST", "/api/admin/status", {})[0] == 401


def test_a_frontchannel_logout_ends_the_session_it_names_for_this_provider(http, provider):
    client = _sign_in_oidc(http, provider, {"sub": "a-1", "sid": "s-1", "preferred_username": "alice"})
    http.get("/api/auth/oidc/frontchannel-logout", params={"iss": "https://evil.example", "sid": "s-1"})
    assert client.call("POST", "/api/admin/status", {})[0] == 200
    response = http.get("/api/auth/oidc/frontchannel-logout", params={"iss": ISSUER, "sid": "s-1"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert client.call("POST", "/api/admin/status", {})[0] == 401


# --- SAML ----------------------------------------------------------------------


def _instant(offset=0):
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _saml_start(http):
    start = http.get("/api/auth/saml/login", follow_redirects=False)
    assert start.status_code == 303
    query = parse_qs(urlparse(start.headers["location"]).query)
    request = etree.fromstring(zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), wbits=-15))
    return request, query["RelayState"][0]


def _assertion(request_id, username="alice", groups=(), audience=SP_ENTITY, recipient=ACS, not_after=300, aid=None, names=None):
    names = names or {"username": 'Name="username"', "groups": 'Name="groups"'}
    attributes = "".join(
        f"<saml:Attribute {names[name]}>"
        + "".join(f"<saml:AttributeValue>{v}</saml:AttributeValue>" for v in values)
        + "</saml:Attribute>"
        for name, values in (("username", [username]), ("groups", list(groups)))
        if values
    )
    return etree.fromstring(
        f'<saml:Assertion xmlns:saml="{SAML}" ID="{aid or "_" + uuid.uuid4().hex}" Version="2.0" IssueInstant="{_instant()}">'
        f"<saml:Issuer>{SAML_ENTITY}</saml:Issuer>"
        f"<saml:Subject><saml:NameID>{username}</saml:NameID>"
        '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData InResponseTo="{request_id}" Recipient="{recipient}" NotOnOrAfter="{_instant(not_after)}"/>'
        "</saml:SubjectConfirmation></saml:Subject>"
        f'<saml:Conditions NotBefore="{_instant(-60)}" NotOnOrAfter="{_instant(not_after)}">'
        f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction></saml:Conditions>"
        f'<saml:AuthnStatement AuthnInstant="{_instant()}" SessionIndex="s-1" SessionNotOnOrAfter="{_instant(3600)}">'
        "<saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:Password"
        "</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
        f"<saml:AttributeStatement>{attributes}</saml:AttributeStatement></saml:Assertion>"
    )


def _sign(provider, element):
    return XMLSigner(
        signature_algorithm="rsa-sha256", digest_algorithm="sha256", c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"
    ).sign(
        element, key=provider.key_pem, cert=provider.cert_pem, reference_uri="#" + element.get("ID")
    )


def _response(provider, request_id, assertion, sign="assertion", destination=ACS):
    response = etree.fromstring(
        f'<samlp:Response xmlns:samlp="{SAMLP}" xmlns:saml="{SAML}" ID="_{uuid.uuid4().hex}" Version="2.0" '
        f'IssueInstant="{_instant()}" Destination="{destination}" InResponseTo="{request_id}">'
        f"<saml:Issuer>{SAML_ENTITY}</saml:Issuer>"
        '<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
        "</samlp:Response>"
    )
    response.append(_sign(provider, assertion) if sign == "assertion" else assertion)
    if sign == "response":
        response = _sign(provider, response)
    return base64.b64encode(etree.tostring(response)).decode()


def _post(http, relay, saml_response):
    return http.post("/api/auth/saml/acs", data={"SAMLResponse": saml_response, "RelayState": relay}, follow_redirects=False)


def _finish(http, acs_response):
    assert acs_response.status_code == 303, acs_response.text
    location = acs_response.headers["location"]
    if location.startswith("/ui/#"):
        return _fragment(acs_response)
    assert location.startswith("/api/auth/saml/done?ticket=")
    return _fragment(http.get(location, follow_redirects=False))


@pytest.mark.parametrize("sign", ["assertion", "response"])
def test_saml_sign_in_asks_for_a_fresh_authentication_and_starts_a_web_sign_in(http, provider, sign, monkeypatch):
    monkeypatch.setattr(settings, "sso_force_login", True)
    request, relay = _saml_start(http)
    assert request.get("ForceAuthn") == "true" and request.get("AssertionConsumerServiceURL") == ACS
    assert request.findtext(f"{{{SAML}}}Issuer") == SP_ENTITY
    assertion = _assertion(request.get("ID"), groups=["admins"])
    kind, grant = _finish(http, _post(http, relay, _response(provider, request.get("ID"), assertion, sign=sign)))
    assert kind == "sso", grant
    response, priv = _register(http, grant)
    client = SignedIn(http, response.json(), priv)
    status, data = client.call("POST", "/api/admin/status", {})
    assert status == 200 and data["username"] == "alice" and data["is_admin"] is True


def test_a_saml_attribute_answers_to_its_friendly_name(http, provider, monkeypatch):
    monkeypatch.setattr(settings, "sso_groups_claim", "memberOf")
    request, relay = _saml_start(http)
    names = {"username": 'Name="urn:oid:0.9.2342.19200300.100.1.1" FriendlyName="username"', "groups": 'Name="urn:oid:1.2.840.113556.1.2.102" FriendlyName="memberOf"'}
    assertion = _assertion(request.get("ID"), username="carol", groups=["admins"], names=names)
    kind, grant = _finish(http, _post(http, relay, _response(provider, request.get("ID"), assertion)))
    assert kind == "sso", grant
    response, priv = _register(http, grant)
    status, data = SignedIn(http, response.json(), priv).call("POST", "/api/admin/status", {})
    assert status == 200 and data["username"] == "carol" and data["is_admin"] is True


def test_saml_names_its_attributes_apart_from_the_openid_connect_claims(http, provider, monkeypatch):
    monkeypatch.setattr(settings, "sso_username_claim", "preferred_username")
    monkeypatch.setattr(settings, "saml_username_attribute", "urn:example:user")
    monkeypatch.setattr(settings, "saml_groups_attribute", "urn:example:groups")
    request, relay = _saml_start(http)
    names = {"username": 'Name="urn:example:user"', "groups": 'Name="urn:example:groups"'}
    assertion = _assertion(request.get("ID"), username="dave", groups=["admins"], names=names)
    kind, grant = _finish(http, _post(http, relay, _response(provider, request.get("ID"), assertion)))
    assert kind == "sso", grant
    response, priv = _register(http, grant)
    status, data = SignedIn(http, response.json(), priv).call("POST", "/api/admin/status", {})
    assert status == 200 and data["username"] == "dave" and data["is_admin"] is True
    # The OpenID Connect sign-in beside it still reads its own claims.
    client = _sign_in_oidc(http, provider, {"sub": "e-1", "preferred_username": "erin", "groups": ["users"]})
    assert client.call("POST", "/api/admin/status", {})[1]["username"] == "erin"


def _refused(http, provider, mutate=None, **kwargs):
    request, relay = _saml_start(http)
    assertion = _assertion(request.get("ID"), **kwargs)
    saml_response = _response(provider, request.get("ID"), assertion)
    if mutate:
        saml_response = mutate(saml_response, request)
    return _finish(http, _post(http, relay, saml_response))


def test_a_saml_response_changed_after_signing_is_refused(http, provider):
    def tamper(saml_response, request):
        return base64.b64encode(base64.b64decode(saml_response).replace(b">alice<", b">tester<", 1)).decode()

    assert _refused(http, provider, tamper) == ("sso-error", "failed")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"audience": "https://other.example/metadata"},
        {"recipient": "https://other.example/acs"},
        {"not_after": -600},
    ],
)
def test_a_signed_assertion_for_elsewhere_or_past_its_time_is_refused(http, provider, kwargs):
    assert _refused(http, provider, **kwargs) == ("sso-error", "failed")


def test_an_assertion_for_another_request_is_refused(http, provider):
    request, relay = _saml_start(http)
    saml_response = _response(provider, "_other", _assertion("_other"))
    assert _finish(http, _post(http, relay, saml_response)) == ("sso-error", "failed")


def test_a_replayed_saml_response_is_refused(http, provider):
    request, relay = _saml_start(http)
    saml_response = _response(provider, request.get("ID"), _assertion(request.get("ID")))
    assert _finish(http, _post(http, relay, saml_response))[0] == "sso"
    assert _finish(http, _post(http, relay, saml_response)) == ("sso-error", "expired")


def test_a_wrapped_forged_assertion_is_refused(http, provider):
    def wrap(saml_response, request):
        document = etree.fromstring(base64.b64decode(saml_response))
        signed = document.find(f"{{{SAML}}}Assertion")
        forged = copy.deepcopy(signed)
        forged.find(f"{{{SAML}}}Subject/{{{SAML}}}NameID").text = "mallory"
        for sig in forged.findall("{http://www.w3.org/2000/09/xmldsig#}Signature"):
            forged.remove(sig)
        document.insert(2, forged)
        return base64.b64encode(etree.tostring(document)).decode()

    def hide(saml_response, request):
        document = etree.fromstring(base64.b64decode(saml_response))
        signed = document.find(f"{{{SAML}}}Assertion")
        document.remove(signed)
        forged = _assertion(request.get("ID"), username="mallory")
        extensions = etree.SubElement(document, f"{{{SAMLP}}}Extensions")
        extensions.append(signed)
        document.append(forged)
        return base64.b64encode(etree.tostring(document)).decode()

    assert _refused(http, provider, wrap) == ("sso-error", "failed")
    assert _refused(http, provider, hide) == ("sso-error", "failed")


def test_an_unsolicited_or_other_browsers_saml_response_is_refused(http, provider):
    request, relay = _saml_start(http)
    saml_response = _response(provider, request.get("ID"), _assertion(request.get("ID")))
    assert _finish(http, _post(http, "unknown", saml_response)) == ("sso-error", "expired")
    acs = _post(http, relay, saml_response)
    with TestClient(http.app) as stranger:
        done = stranger.get(acs.headers["location"], follow_redirects=False)
        assert _fragment(done) == ("sso-error", "browser")


def _logout_query(provider, name_id, sign=True):
    request = (
        f'<samlp:LogoutRequest xmlns:samlp="{SAMLP}" xmlns:saml="{SAML}" ID="_{uuid.uuid4().hex}" Version="2.0" '
        f'IssueInstant="{_instant()}" Destination="{BASE}/api/auth/saml/slo">'
        f"<saml:Issuer>{SAML_ENTITY}</saml:Issuer><saml:NameID>{name_id}</saml:NameID>"
        "<samlp:SessionIndex>s-1</samlp:SessionIndex></samlp:LogoutRequest>"
    )
    compressor = zlib.compressobj(wbits=-15)
    encoded = base64.b64encode(compressor.compress(request.encode()) + compressor.flush()).decode()
    query = urlencode({"SAMLRequest": encoded, "RelayState": "r", "SigAlg": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"})
    if sign:
        signature = provider.key.sign(query.encode(), padding.PKCS1v15(), hashes.SHA256())
        query += "&Signature=" + quote(base64.b64encode(signature).decode(), safe="")
    return query


def test_a_signed_logout_request_ends_the_saml_sign_in(http, provider):
    request, relay = _saml_start(http)
    kind, grant = _finish(http, _post(http, relay, _response(provider, request.get("ID"), _assertion(request.get("ID")))))
    response, priv = _register(http, grant)
    client = SignedIn(http, response.json(), priv)
    assert client.call("POST", "/api/admin/status", {})[0] == 200
    assert http.get("/api/auth/saml/slo?" + _logout_query(provider, "alice", sign=False), follow_redirects=False).status_code == 400
    assert client.call("POST", "/api/admin/status", {})[0] == 200
    reply = http.get("/api/auth/saml/slo?" + _logout_query(provider, "alice"), follow_redirects=False)
    assert reply.status_code == 303 and reply.headers["location"].startswith("https://idp.example/saml/slo?SAMLResponse=")
    assert client.call("POST", "/api/admin/status", {})[0] == 401


def test_a_malformed_logout_request_is_refused(http, provider):
    for query in ("SAMLRequest=%%%&Signature=!!&SigAlg=x", "SAMLRequest=bm90IGRlZmxhdGVk"):
        assert http.get("/api/auth/saml/slo?" + query, follow_redirects=False).status_code == 400
    response = http.post("/api/auth/saml/slo", data={"SAMLRequest": "not base64!"}, follow_redirects=False)
    assert response.status_code == 400


def test_the_sp_metadata_names_the_endpoints_the_browser_reaches(http):
    metadata = etree.fromstring(http.get("/api/auth/saml/metadata").content)
    assert metadata.get("entityID") == SP_ENTITY
    md = "{urn:oasis:names:tc:SAML:2.0:metadata}"
    assert metadata.find(f"{md}SPSSODescriptor/{md}AssertionConsumerService").get("Location") == ACS
