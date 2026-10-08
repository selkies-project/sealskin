"""The cluster: peer signatures, joining, placement, limits, and the web sign-in of the plain lane."""

import hashlib
import time

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import cluster, persistence, proxy_auth, quota, security, sso, store, user_manager
from app.settings import settings
from app.state import state
from tests.conftest import PROXY_SECRET, WEB
from tests.test_api_smoke import _pem_pair

PEER = {"X-SealSkin-Secret": PROXY_SECRET, "X-SealSkin-Listener": "peer"}


@pytest.fixture
def node(tmp_path, monkeypatch):
    """This process as a started node, with its API."""
    _key, private, _public = _pem_pair()
    key_path = tmp_path / "server_key.pem"
    key_path.write_text(private)
    monkeypatch.setattr(settings, "server_private_key_path", str(key_path))
    monkeypatch.setattr(settings, "auto_update_apps", False)
    monkeypatch.setattr(settings, "watch_config_files", False)
    monkeypatch.setattr(settings, "ui_path", str(tmp_path / "no-ui"))
    monkeypatch.setattr(settings, "root_token", "a-root-token-for-the-tests")
    import app.api as api_module

    security.init_server_keys()
    sso._KEYS.clear()
    sso._ROOT_FAILURES.clear()
    with TestClient(api_module.api_app) as client:
        yield client


def _other_node(name="bravo", roles=("runtime",), approved=True, pool="default"):
    """Add the record of a second node, whose key this test holds, and return `(id, private_pem)`."""
    key, private, public = _pem_pair()
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    node_id = hashlib.sha256(der).hexdigest()[:16]
    cluster.NODES[node_id] = {
        "id": node_id, "name": name, "public_key": public, "address": f"{name}:8444", "roles": list(roles),
        "approved": approved, "pool": pool, "peer_cert": "",
    }  # fmt: skip
    return node_id, key


def _signed(key, node_id, method, path, body=b"", act=None):
    """Sign a peer request as the node `node_id` whose key is `key`."""
    real, cluster.NODE_ID, state_key = cluster.NODE_ID, node_id, state.server_private_key
    state.server_private_key = key
    try:
        return cluster.sign_request(real, method, path, body, act)
    finally:
        cluster.NODE_ID, state.server_private_key = real, state_key


def _status(node_id, sessions=(), gpus=(), **extra):
    cluster.PEERS[node_id] = {
        "seen": time.time(), "missed": 0,
        "status": {"roles": cluster.NODES[node_id]["roles"], "cpus": 4, "sessions": list(sessions), "gpus": list(gpus), **extra},
    }  # fmt: skip


def test_the_first_node_of_a_store_approves_itself_and_knows_who_it_is(node):
    assert cluster.NODE_ID and cluster.is_approved() and not cluster.is_clustered()
    record = persistence.read_yaml(cluster._node_path(cluster.NODE_ID))
    assert record["approved"] is True and record["pool"] == "default" and "BEGIN CERTIFICATE" in record["peer_cert"]


def test_a_peer_request_is_taken_only_signed_by_an_approved_node_on_the_peer_listener(node):
    node_id, key = _other_node()
    token = _signed(key, node_id, "GET", "/peer/status")
    assert node.get("/peer/status", headers={"Authorization": token}).status_code == 401
    assert node.get("/peer/status", headers={**PEER, "Authorization": token}).status_code == 200
    assert node.get("/peer/status", headers={**PEER, "Authorization": token}).status_code == 401
    other = _signed(key, node_id, "POST", "/peer/notify")
    assert node.get("/peer/status", headers={**PEER, "Authorization": other}).status_code == 401
    cluster.NODES[node_id]["approved"] = False
    fresh = _signed(key, node_id, "GET", "/peer/status")
    assert node.get("/peer/status", headers={**PEER, "Authorization": fresh}).status_code == 403


def test_only_a_frontend_may_act_for_a_user(node):
    user_manager.write_user_file("alice", "", user_manager.DEFAULT_USER_SETTINGS.copy())
    user_manager.load_users_and_groups()
    act = {"username": "alice", "provider_groups": [], "is_admin": False, "via": "oidc"}
    runtime_id, runtime_key = _other_node("bravo", roles=("runtime",))
    token = _signed(runtime_key, runtime_id, "POST", "/api/admin/status", b"{}", act)
    refused = node.post("/api/admin/status", content=b"{}", headers={**PEER, "Authorization": token})
    assert refused.status_code == 403
    front_id, front_key = _other_node("charlie", roles=("frontend",))
    token = _signed(front_key, front_id, "POST", "/api/admin/status", b"{}", act)
    answer = node.post("/api/admin/status", content=b"{}", headers={**PEER, "Authorization": token})
    assert answer.status_code == 200 and answer.json()["username"] == "alice" and answer.json()["via"] == "oidc"
    tampered = _signed(front_key, front_id, "POST", "/api/admin/status", b"{}", act)
    assert node.post("/api/admin/status", content=b'{"x":1}', headers={**PEER, "Authorization": tampered}).status_code == 401


def test_a_join_code_admits_one_node_that_proves_it(node):
    issued = cluster.create_join_code("gpu")
    code_id, secret = issued["code"].split(".", 1)
    node_id, key = _other_node(approved=False)
    record = dict(cluster.self_record(), id=node_id, name="bravo", public_key=cluster.NODES.pop(node_id)["public_key"])
    proof = cluster._proof(cluster._join_key(secret), record)
    wrong = cluster._proof(cluster._join_key("guess"), record)
    assert node.post("/peer/join", json={"code_id": code_id, "record": record, "proof": wrong}, headers=PEER).status_code == 403
    assert node.post("/peer/join", json={"code_id": code_id, "record": record, "proof": proof}).status_code == 404
    admitted = node.post("/peer/join", json={"code_id": code_id, "record": record, "proof": proof}, headers=PEER)
    assert admitted.status_code == 200
    body = admitted.json()
    assert body["store_node"]["id"] == cluster.NODE_ID
    assert body["proof"] == cluster._proof(cluster._join_key(secret), body["store_node"])
    assert cluster.NODES[node_id]["approved"] is True and cluster.NODES[node_id]["pool"] == "gpu"
    assert node.post("/peer/join", json={"code_id": code_id, "record": record, "proof": proof}, headers=PEER).status_code == 403


def test_the_keeper_serves_shared_objects_to_peers_within_what_each_may_write(node):
    node_id, key = _other_node("bravo", roles=("runtime",))

    def send(method, path, body=b"", headers=None):
        token = _signed(key, node_id, method, path, body)
        return node.request(method, path, content=body, headers={**PEER, "Authorization": token, **(headers or {})})

    assert send("GET", "/peer/store/cluster/root.yml").status_code == 200
    assert send("GET", "/peer/store/sessions.yml").status_code == 400
    assert f"cluster/nodes/{cluster.NODE_ID}.yml" in send("GET", "/peer/store/", headers={"x-sealskin-list": "cluster/"}).json()
    assert send("PUT", "/peer/store/keys/users/mallory", b"x").status_code == 403
    own = f"/peer/store/cluster/nodes/{node_id}.yml"
    assert send("PUT", own, b"id: x\n", {"if-none-match": "*"}).status_code == 200
    assert send("PUT", own, b"id: y\n", {"if-none-match": "*"}).status_code == 412


def _user(username="alice", **effective):
    return {"username": username, "is_admin": False, "effective_settings": {**user_manager.DEFAULT_USER_SETTINGS, **effective}}


def test_placement_follows_the_home_then_pool_access_then_room_and_load(node):
    bravo, _ = _other_node("bravo")
    charlie, _ = _other_node("charlie", pool="gpu")
    busy = [{"session_id": str(n), "username": "x", "gpu": False} for n in range(3)]
    _status(bravo, sessions=busy)
    _status(charlie, gpus=[{"device": "/dev/dri/renderD128"}])
    assert cluster.choose_node(_user()) == cluster.NODE_ID
    cluster.HOMES["alice"] = {"work": bravo}
    assert cluster.choose_node(_user(), home_name="work") == bravo
    cluster.PEERS[bravo]["missed"] = cluster.MISSED_POLLS
    with pytest.raises(HTTPException) as away:
        cluster.choose_node(_user(), home_name="work")
    assert away.value.status_code == 503
    cluster.POOLS["gpu"] = {"restricted": True, "groups": ["render"]}
    with pytest.raises(HTTPException) as closed:
        cluster.choose_node(_user(), pool="gpu")
    assert closed.value.status_code == 403
    assert cluster.choose_node(_user(groups=["render"]), pool="gpu") == charlie
    assert cluster.choose_node(_user(groups=["render"], pools_denied=["gpu"]), wants_gpu=False) == cluster.NODE_ID
    state.available_gpus.clear()
    assert cluster.choose_node(_user(groups=["render"]), wants_gpu=True) == charlie
    cluster.PEERS[charlie]["status"]["sessions"] = [{"session_id": "g", "username": "y", "gpu": True, "gpu_exclusive": True}]
    with pytest.raises(HTTPException):
        cluster.choose_node(_user(groups=["render"]), wants_gpu=True)


async def test_limits_count_sessions_on_every_node_and_spend_the_allowance(node):
    bravo, _ = _other_node("bravo")
    _status(bravo, sessions=[{"session_id": "1", "username": "alice", "gpu": False}])
    cluster.PEERS[bravo]["missed"] = cluster.MISSED_POLLS
    with pytest.raises(HTTPException) as limited:
        await quota.check_launch(_user(session_limit=1))
    assert "Session limit" in limited.value.detail
    await quota.check_launch(_user(session_limit=2))
    await quota.check_launch(dict(_user(session_limit=0), is_admin=True))
    cluster.POOLS["default"] = {"cost": 2.0, "gpu_cost": 8.0}
    state.sessions["s1"] = {"username": "alice", "created_at": time.time()}
    state.sessions["s2"] = {"username": "alice", "created_at": time.time(), "gpu_config": {"device": "x"}}
    quota._last_tick = time.time() - 180
    quota.tick()
    assert quota.used_hours("alice", "day") == pytest.approx(180 * 10 / 3600, rel=0.05)
    await quota.flush()
    written = persistence.read_yaml(f"{settings.cluster_path}/usage/{quota._today()}/{cluster.NODE_ID}.yml")
    assert written["alice"] == pytest.approx(1800, rel=0.05)
    with pytest.raises(HTTPException) as spent:
        await quota.check_launch(_user(allowance_hours=0.4, allowance_period="week"))
    assert "allowance" in spent.value.detail
    await quota.check_launch(_user(allowance_hours=1))


def test_session_resources_are_capped_below_what_the_app_asks():
    overrides = {"mem_limit": "8g", "nano_cpus": 6_000_000_000}
    quota.cap_resources(overrides, {"session_cpus": 2, "session_memory_mb": 4096})
    assert overrides == {"mem_limit": 4096 * 1024 * 1024, "nano_cpus": 2_000_000_000}
    small = {"mem_limit": "512m"}
    quota.cap_resources(small, {"session_cpus": -1, "session_memory_mb": 4096})
    assert small == {"mem_limit": 512 * 1024 * 1024}


def test_root_signs_in_with_the_token_and_the_plain_lane_needs_the_proxy_and_the_apps_own_origin(node):
    assert node.post("/api/auth/root", json={"token": "a-root-token-for-the-tests"}).status_code == 403
    assert node.post("/api/auth/root", json={"token": "wrong"}, headers=WEB).status_code == 403
    signed_in = node.post("/api/auth/root", json={"token": "a-root-token-for-the-tests"}, headers=WEB)
    assert signed_in.status_code == 200 and "HttpOnly" in signed_in.headers["set-cookie"]
    assert signed_in.headers["set-cookie"].startswith(f"{sso.SESSION_COOKIE}=")
    cookie = {"Cookie": signed_in.headers["set-cookie"].split(";", 1)[0]}
    status = node.post("/api/admin/status", json={}, headers={**WEB, **cookie})
    assert status.status_code == 200 and status.json()["username"] == "root" and status.json()["is_admin"] is True
    assert status.json()["via"] == "root"
    assert node.post("/api/admin/status", json={}, headers=cookie).status_code in (400, 401)
    sibling = {**WEB, **cookie, "Sec-Fetch-Site": "same-site"}
    assert node.post("/api/admin/status", json={}, headers=sibling).status_code == 403
    assert node.get("/api/admin/cluster", headers={**WEB, **cookie}).json()["store"]["kind"] == "file"
    assert node.post("/api/auth/signout", json={}, headers={**WEB, **cookie}).status_code == 204
    assert node.post("/api/admin/status", json={}, headers={**WEB, **cookie}).status_code == 401
    assert store.get("cluster/root.yml")[0].count(b"a-root-token") == 0


def _entrance(monkeypatch, handler):
    """Stand `handler` in for the node's public entrance, which the proxy check asks."""
    monkeypatch.setattr(settings, "proxy_auth_user_header", "Remote-User")
    monkeypatch.setattr(settings, "proxy_auth_groups_header", "Remote-Groups")
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    monkeypatch.setattr(settings, "public_url", "https://sealskin.example")
    monkeypatch.setattr(proxy_auth, "_transport", httpx.MockTransport(handler))
    proxy_auth._result.clear()
    proxy_auth._forged.clear()
    monkeypatch.setattr(proxy_auth, "_cookies", "")


def _signs_in(request):
    """A proxy that sends a visitor with no sign-in to its own page."""
    return httpx.Response(302, headers={"Location": "https://auth.example/"})


NAMED = {**WEB, "Remote-User": "alice", "Remote-Groups": "staff, admins", "X-SealSkin-Remote": "10.1.2.3"}


def test_a_trusted_proxy_signs_users_in_and_no_one_else_can_name_one(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    monkeypatch.setattr(settings, "sso_admin_group", "admins")
    assert node.post("/api/admin/status", json={}, headers={**NAMED, "X-SealSkin-Remote": "203.0.113.9"}).status_code == 401
    answer = node.post("/api/admin/status", json={}, headers=NAMED)
    assert answer.status_code == 200 and answer.json()["username"] == "alice" and answer.json()["is_admin"] is True
    assert answer.json()["via"] == "proxy"
    assert user_manager.get_user("alice")["settings"]["provider_groups"] == ["admins", "staff"]
    forged = {k: v for k, v in NAMED.items() if k != "X-SealSkin-Secret"}
    assert node.post("/api/admin/status", json={}, headers=forged).status_code in (400, 401)


def test_a_proxy_that_passes_a_visitors_own_header_on_signs_nobody_in(node, monkeypatch):
    def passes_on(request):
        # What the node's own answer would be to the request the proxy passed on untouched.
        assert request.headers["remote-user"] == request.headers["remote-groups"] == proxy_auth.FORGED
        return httpx.Response(200, json={"sealskin": "proxy-check", "forged": ["Remote-User", "Remote-Groups"], "trusted": True})

    _entrance(monkeypatch, passes_on)
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 401
    assert proxy_auth._result["state"] == "open"
    monkeypatch.setattr(settings, "proxy_auth_unchecked", True)
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 200


def test_a_proxy_that_drops_the_header_of_a_request_it_lets_through_is_believed(node, monkeypatch):
    _entrance(monkeypatch, lambda request: httpx.Response(200, json={"sealskin": "proxy-check", "forged": [], "trusted": True}))
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 200


def test_a_node_that_cannot_reach_its_entrance_believes_the_proxy_only_when_told_to(node, monkeypatch):
    asked = []

    def away(request):
        asked.append(str(request.url))
        raise httpx.ConnectError("no route")

    _entrance(monkeypatch, away)
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8, 10.1.2.3")
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 401
    assert proxy_auth._result["state"] == "unreachable"
    # The public URL first, then the one address named alone, under the public name.
    assert asked == ["https://sealskin.example/api/auth/proxy", "https://10.1.2.3/api/auth/proxy"]
    monkeypatch.setattr(settings, "proxy_auth_unchecked", True)
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 200


def test_a_session_on_a_trusted_network_cannot_name_a_user(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    monkeypatch.setitem(state.sessions, "123e4567-e89b-12d3-a456-426614174000", {"ip": "10.1.2.3"})
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 401
    assert node.post("/api/admin/status", json={}, headers={**NAMED, "X-SealSkin-Remote": "10.1.2.4"}).status_code == 200


def test_a_proxys_groups_are_read_whichever_way_it_separates_them(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    monkeypatch.setattr(settings, "sso_admin_group", "admins")
    answer = node.post("/api/admin/status", json={}, headers={**NAMED, "Remote-Groups": "staff|admins"})
    assert answer.status_code == 200 and answer.json()["is_admin"] is True
    assert user_manager.get_user("alice")["settings"]["provider_groups"] == ["admins", "staff"]


def test_a_proxys_user_is_told_where_signing_out_happens(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    monkeypatch.setattr(settings, "proxy_auth_logout_url", "https://auth.example/logout")
    assert node.post("/api/admin/status", json={}, headers=NAMED).json()["sign_out_url"] == "https://auth.example/logout"


def test_the_address_of_a_request_is_what_this_nodes_proxy_resolved(node):
    seen = {}

    @node.app.get("/_address")
    def address(request: Request):
        seen["address"] = cluster.client_address(request)
        return {}

    node.get("/_address", headers={**WEB, "X-SealSkin-Client": "198.51.100.7", "X-Forwarded-For": "6.6.6.6"})
    assert seen["address"] == "198.51.100.7"
    # A session on a network trusted whole says nothing about where a request came from.
    state.sessions["123e4567-e89b-12d3-a456-426614174000"] = {"ip": "10.1.2.3"}
    node.get("/_address", headers={**WEB, "X-SealSkin-Remote": "10.1.2.3", "X-SealSkin-Client": "198.51.100.7"})
    state.sessions.clear()
    assert seen["address"] == "10.1.2.3"
    # Past the proxy secret, the header is anyone's to write.
    node.get("/_address", headers={"X-SealSkin-Client": "198.51.100.7"})
    assert seen["address"] != "198.51.100.7"


def test_the_http_listener_opens_only_for_trusted_proxies(monkeypatch):
    from app import main

    monkeypatch.setattr(settings, "http_port", 8080)
    monkeypatch.setattr(settings, "trusted_proxies", "")
    assert main.http_listener() is False
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.5")
    assert main.http_listener() is True
    site = main.HTTP_SITE.format(port=8080, networks="10.0.0.5", public="https://sealskin.example")
    assert "not remote_ip 10.0.0.5" in site and "redir https://sealskin.example{uri} 308" in site
    monkeypatch.setattr(settings, "http_port", 0)
    assert main.http_listener() is False


def test_the_check_path_says_what_arrived(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    seen = node.get("/api/auth/proxy", headers={"Remote-User": "mallory", "X-SealSkin-Remote": "10.1.2.3"}).json()
    assert seen == {
        "sealskin": "proxy-check", "headers": ["Remote-User", "Remote-Groups"], "user": "mallory", "groups": "",
        "forged": [], "trusted": True,
    }  # fmt: skip
    assert node.get("/api/auth/proxy", headers={"X-SealSkin-Remote": "203.0.113.9"}).json()["trusted"] is False


def test_a_groups_header_the_proxy_leaves_to_the_browser_ends_header_sign_ins(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 200
    # The web app's request from a signed-in browser: the proxy set the user and passed the groups on.
    forged = {"Remote-User": "alice", "Remote-Groups": proxy_auth.FORGED, "X-SealSkin-Remote": "10.1.2.3"}
    assert node.get("/api/auth/proxy", headers=forged).json()["forged"] == ["Remote-Groups"]
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 401
    # The same from anywhere but a trusted proxy says nothing about the proxy.
    proxy_auth._forged.clear()
    node.get("/api/auth/proxy", headers={**forged, "X-SealSkin-Remote": "203.0.113.9"})
    assert node.post("/api/admin/status", json={}, headers=NAMED).status_code == 200


async def test_a_test_counts_only_the_forged_headers_of_its_own_moment(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    proxy_auth._forged["Remote-Groups"] = time.time() - 60
    assert (await proxy_auth.check())["state"] == "open"
    assert (await proxy_auth.check(fresh=True))["state"] == "guarded"
    proxy_auth._forged["Remote-Groups"] = time.time()
    assert (await proxy_auth.check(fresh=True))["state"] == "open"


def test_the_first_request_a_proxy_signed_in_has_the_proxy_tried_with_its_cookies(node, monkeypatch):
    def sets_the_user_alone(request):
        # Guarded against a visitor with no sign-in; with one, the groups header goes through as sent.
        if "cookie" not in request.headers:
            return httpx.Response(401)
        assert request.headers["cookie"] == "authelia_session=valid"
        return httpx.Response(200, json={"sealskin": "proxy-check", "forged": ["Remote-Groups"], "trusted": True})

    _entrance(monkeypatch, sets_the_user_alone)
    claimed = {**NAMED, "Remote-Groups": "admins", "Cookie": "authelia_session=valid"}
    assert node.post("/api/admin/status", json={}, headers=claimed).status_code == 401
    assert proxy_auth._result["state"] == "open" and "Remote-Groups" in proxy_auth._result["detail"]


def test_no_groups_are_taken_from_a_proxy_until_a_header_is_named(node, monkeypatch):
    _entrance(monkeypatch, _signs_in)
    monkeypatch.setattr(settings, "proxy_auth_groups_header", "")
    monkeypatch.setattr(settings, "sso_admin_group", "admins")
    answer = node.post("/api/admin/status", json={}, headers=NAMED)
    assert answer.status_code == 200 and answer.json()["is_admin"] is False


async def test_deleting_a_user_forgets_its_homes_here_and_on_the_nodes_that_hold_them(node, monkeypatch):
    other, _key = _other_node("bravo")
    cluster.record_home("alice", "work", cluster.NODE_ID)
    cluster.record_home("alice", "games", other)
    asked = []

    async def call(node_id, method, path, **_kwargs):
        asked.append((node_id, method, path))
        return httpx.Response(204)

    monkeypatch.setattr(cluster, "call", call)
    await cluster.forget_user("alice")
    assert asked == [(other, "DELETE", "/peer/users/alice")]
    assert "alice" not in cluster.HOMES and not persistence.exists(f"{settings.cluster_path}/homes/alice.yml")


def test_only_a_frontend_has_another_node_drop_a_users_storage(node):
    import os

    home = os.path.join(settings.storage_path, "alice", "games")
    os.makedirs(home)

    def drop(roles):
        node_id, key = _other_node("charlie", roles=roles)
        token = _signed(key, node_id, "DELETE", "/peer/users/alice")
        return node.delete("/peer/users/alice", headers={**PEER, "Authorization": token}).status_code

    assert drop(("runtime",)) == 403 and os.path.isdir(home)
    assert drop(("frontend",)) == 204 and not os.path.exists(os.path.dirname(home))


def test_a_frontend_hands_an_upload_over_file_by_file(node):
    front_id, front_key = _other_node("charlie", roles=("frontend",))
    upload = "123e4567-e89b-12d3-a456-426614174000"

    def put(name, body=b"data"):
        path = f"/peer/upload/alice/{upload}/{name}"
        token = _signed(front_key, front_id, "PUT", path, body)
        return node.put(path, content=body, headers={**PEER, "Authorization": token}).status_code

    assert put("chunk_0") == 204 and put("metadata.json") == 204
    assert put("..%2Fescape") in (400, 404) and put("chunk_x") == 400
    with open(f"{settings.upload_dir}/alice/{upload}/chunk_0", "rb") as handle:
        assert handle.read() == b"data"


def test_a_launch_reports_its_stages_to_the_user_who_started_it(node):
    from app import progress

    launch = "123e4567-e89b-12d3-a456-426614174abc"
    assert progress.begin("not-a-uuid", "alice") is None
    progress.step("storage")
    assert progress.begin(launch, "alice") == launch
    progress.step("image", image="example/app:latest")
    assert progress.get(launch, "bob") is None and progress.begin(launch, "bob") is None
    progress.begin(launch, "alice")
    seen = progress.view(progress.get(launch, "alice"))
    assert seen["stage"] == "image" and seen["detail"] == {"image": "example/app:latest"}
    progress.finish({"session_url": "/x/?access_token=t", "session_id": "x"})
    progress.step("waiting")
    done = progress.view(progress.get(launch, "alice"))
    assert done["stage"] == "ready" and done["session_id"] == "x"
    other = "123e4567-e89b-12d3-a456-426614174abd"
    progress.begin(other, "alice")
    progress.finish(error="No node open to this account has room for the session.")
    assert progress.view(progress.get(other, "alice"))["error"].startswith("No node")


def test_a_refused_launch_is_reported_as_failed_and_only_to_its_owner(node):
    signed_in = node.post("/api/auth/root", json={"token": "a-root-token-for-the-tests"}, headers=WEB)
    cookie = {**WEB, "Cookie": signed_in.headers["set-cookie"].split(";", 1)[0]}
    launch = "123e4567-e89b-12d3-a456-426614174abe"
    assert node.get(f"/api/launch/progress/{launch}", headers=cookie).status_code == 404
    refused = node.post("/api/launch/simple", json={"application_id": "missing", "launch_id": launch}, headers=cookie)
    assert refused.status_code == 404
    seen = node.get(f"/api/launch/progress/{launch}", headers=cookie)
    assert seen.status_code == 200 and seen.json()["stage"] == "failed" and "not found" in seen.json()["error"]
    assert node.get(f"/api/launch/progress/{launch}").status_code in (400, 401)


def test_the_audit_log_is_searched_and_paged_newest_first(node):
    from app import audit

    for n in range(7):
        audit.record("launch", "alice" if n % 2 else "bob", session=f"s{n}", app="Firefox")
    audit.record("admin", "root", method="PUT", path="/api/admin/groups/staff")
    signed_in = node.post("/api/auth/root", json={"token": "a-root-token-for-the-tests"}, headers=WEB)
    cookie = {**WEB, "Cookie": signed_in.headers["set-cookie"].split(";", 1)[0]}
    page = node.get("/api/admin/cluster/audit?limit=3", headers=cookie).json()
    assert page["total"] == 9 and len(page["events"]) == 3 and page["events"][0]["event"] == "sign_in"
    assert page["days"] == [audit.datetime.datetime.now(audit.datetime.UTC).strftime("%Y-%m-%d")]
    found = node.get("/api/admin/cluster/audit?q=ALICE+firefox&limit=50", headers=cookie).json()
    assert found["total"] == 3 and {e["user"] for e in found["events"]} == {"alice"}
    later = node.get("/api/admin/cluster/audit?q=launch&offset=5&limit=5", headers=cookie).json()
    assert later["total"] == 7 and len(later["events"]) == 2
    assert node.get("/api/admin/cluster/audit?day=2020-01-01", headers=cookie).json()["total"] == 0
    assert node.get("/api/admin/cluster/audit?since=2020-01-01&q=staff", headers=cookie).json()["total"] == 1


def test_an_app_laboratory_session_is_one_per_admin_and_in_no_session_list(node):
    signed_in = node.post("/api/auth/root", json={"token": "a-root-token-for-the-tests"}, headers=WEB)
    cookie = {**WEB, "Cookie": signed_in.headers["set-cookie"].split(";", 1)[0]}
    assert node.get("/api/admin/lab", headers=cookie).json() == {"session": None}
    state.sessions["11111111-1111-4111-8111-111111111111"] = {
        "username": "root", "lab": True, "provider_app_id": "meta", "app_name": "Meta", "app_logo": "",
        "created_at": time.time(), "access_token": "t", "instance_id": "",
    }  # fmt: skip
    state.sessions["22222222-2222-4222-8222-222222222222"] = dict(
        state.sessions["11111111-1111-4111-8111-111111111111"], lab=False, app_name="Plain"
    )
    open_lab = node.get("/api/admin/lab", headers=cookie).json()["session"]
    assert open_lab["app_name"] == "Meta" and open_lab["session_url"].startswith("/11111111-")
    assert [s["app_name"] for s in node.get("/api/sessions", headers=cookie).json()] == ["Plain"]
    listed = node.get("/api/admin/sessions", headers=cookie).json()
    assert [s["app_name"] for u in listed for s in u["sessions"]] == ["Plain"]
    assert [s["session_id"][:8] for s in cluster.sessions_of("root")] == ["22222222"]
    again = node.post("/api/admin/launch/meta_customize", json={"application_id": "meta"}, headers=cookie)
    assert again.status_code == 409 and "Meta" in again.json()["detail"]
