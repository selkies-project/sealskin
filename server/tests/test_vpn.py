"""VPN profiles: importing provider bundles, sealing their secrets, who may pick what, and the gateway's files."""

import io
import zipfile

import pytest
import yaml

from app import filecrypt, vpn
from app.settings import settings
from app.state import state

OVPN_BODY = """client
dev tun
proto udp
resolv-retry infinite
remote-random
nobind
persist-key
auth-user-pass
cipher AES-256-GCM
verb 3
<ca>
-----BEGIN CERTIFICATE-----
MIIFCjCCAvKgAwIBAgIBATANBgkqhkiG9w0BAQ0FADA5
-----END CERTIFICATE-----
</ca>
<tls-auth>
-----BEGIN OpenVPN Static key V1-----
e685bdaf659a25a200e2b9e39e51ff03
-----END OpenVPN Static key V1-----
</tls-auth>
key-direction 1
"""

WG_INTERFACE = """[Interface]
PrivateKey = cGVlci1wcml2YXRlLWtleS1wZWVyLXByaXZhdGUta2V5MQ=
Address = 10.64.0.5/32
DNS = 10.64.0.1
"""


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _nord_zip(servers=("us100", "us101", "de200")) -> bytes:
    files = {}
    for server in servers:
        files[f"ovpn_udp/{server}.example.com.udp.ovpn"] = f"# comment\nremote {server}.example.com 1194\n{OVPN_BODY}"
        files[f"ovpn_tcp/{server}.example.com.tcp.ovpn"] = f"remote {server}.example.com 443\nproto tcp\n{OVPN_BODY.replace('proto udp', 'proto tcp')}"
    return _zip(files)


def test_a_provider_bundle_is_one_template_with_an_endpoint_per_file_and_protocol():
    (group,) = vpn.parse_bundle("ovpn.zip", _nord_zip())
    assert group.kind == "openvpn" and group.needs_credentials
    assert "remote " not in group.template and "proto " not in group.template and "remote-random" not in group.template
    assert "<ca>" in group.template and "auth-user-pass\n" in group.template
    # The TCP and UDP folders differ in the server line alone, so they are endpoints of one profile.
    assert [(e["label"], e["port"]) for e in group.endpoints] == [
        ("de200.example.com:443/tcp", 443),
        ("us100.example.com:443/tcp", 443),
        ("us101.example.com:443/tcp", 443),
        ("de200.example.com:1194/udp", 1194),
        ("us100.example.com:1194/udp", 1194),
        ("us101.example.com:1194/udp", 1194),
    ]


def test_imported_profiles_are_named_after_the_upload():
    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(), username="svc", password="pw")
    assert record["name"] == "Nord"
    assert record["needs_credentials"] and record["published"] == [] and record["users"] == ["all"]
    assert vpn._secret(record)["username"] == "svc"
    assert set(state.vpn_profiles) == {record["id"]}


def test_a_template_that_asks_for_credentials_needs_them():
    with pytest.raises(ValueError, match="username and password"):
        vpn.import_profiles("Nord", "ovpn.zip", _nord_zip())


def test_one_file_with_repeated_remotes_gives_distinct_endpoints_labelled_by_port():
    text = "client\nremote vpn.example.com 1194 udp\nremote vpn.example.com 1194 udp\nremote vpn.example.com 443 tcp\n<ca>\nx\n</ca>\n"
    (group,) = vpn.parse_bundle("profile.ovpn", text.encode())
    assert [(e["host"], e["port"], e["proto"], e["label"]) for e in group.endpoints] == [
        ("vpn.example.com", 1194, "udp", "vpn.example.com:1194/udp"),
        ("vpn.example.com", 443, "tcp", "vpn.example.com:443/tcp"),
    ]
    assert not group.needs_credentials


def test_split_certificate_files_are_inlined_from_the_bundle():
    files = {
        "OpenVPN256/openvpn.ovpn": "client\nremote 1.2.3.4 443\nproto tcp\nca ca.crt\ncert client.crt\nkey client.key\ntls-auth ta.key 1\n",
        "OpenVPN256/ca.crt": "CA\n",
        "OpenVPN256/client.crt": "CERT\n",
        "OpenVPN256/client.key": "KEY\n",
        "OpenVPN256/ta.key": "TA\n",
    }
    (group,) = vpn.parse_bundle("cyberghost.zip", _zip(files))
    assert "<ca>\nCA\n</ca>" in group.template and "<key>\nKEY\n</key>" in group.template
    assert "<tls-auth>\nTA\n</tls-auth>\nkey-direction 1" in group.template
    assert "ca ca.crt" not in group.template
    assert group.endpoints[0]["label"] == "openvpn"


def test_a_missing_certificate_file_is_refused():
    with pytest.raises(ValueError, match="not in the upload"):
        vpn.parse_bundle("x.ovpn", b"client\nremote a.b 1194\nca missing.crt\n")


def test_wireguard_files_sharing_an_interface_are_one_profile_with_a_peer_each():
    files = {
        "se-sto-001.conf": WG_INTERFACE + "\n[Peer]\nPublicKey = peer1\nPresharedKey = psk1\nAllowedIPs = 0.0.0.0/0\nEndpoint = 185.1.1.1:51820\n",
        "se-sto-002.conf": WG_INTERFACE + "[Peer]\nPublicKey = peer2\nAllowedIPs = 0.0.0.0/0,::/0\nEndpoint = [2a03::1]:51820\n",
    }
    (group,) = vpn.parse_bundle("mullvad.zip", _zip(files))
    assert group.kind == "wireguard"
    assert group.template == "PrivateKey = cGVlci1wcml2YXRlLWtleS1wZWVyLXByaXZhdGUta2V5MQ=\nAddress = 10.64.0.5/32\nDNS = 10.64.0.1\n"
    assert [(e["host"], e["port"], e["public_key"], e["preshared_key"], e["label"]) for e in group.endpoints] == [
        ("185.1.1.1", 51820, "peer1", "psk1", "se-sto-001"),
        ("2a03::1", 51820, "peer2", "", "se-sto-002"),
    ]
    (record,) = vpn.import_profiles("Mullvad", "mullvad.zip", _zip(files))
    secret = vpn._secret(record)
    assert secret["preshared_keys"] == {record["endpoints"][0]["id"]: "psk1"}
    assert "preshared_key" not in record["endpoints"][0]


def test_wireguard_files_with_their_own_keys_are_profiles_of_their_own():
    files = {
        "a.conf": WG_INTERFACE + "[Peer]\nPublicKey = p\nEndpoint = h:1\n",
        "b.conf": WG_INTERFACE.replace("MQ=", "Mg=") + "[Peer]\nPublicKey = p\nEndpoint = h:1\n",
    }
    groups = vpn.parse_bundle("proton.zip", _zip(files))
    assert len(groups) == 2
    records = vpn.import_profiles("Proton", "proton.zip", _zip(files))
    assert sorted(r["name"] for r in records) == ["Proton a", "Proton b"]


def test_the_chosen_type_must_match_the_config():
    with pytest.raises(ValueError, match="is an OpenVPN config; pick that type"):
        vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="u", password="p", kind="wireguard")
    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="u", password="p", kind="openvpn")
    assert record["kind"] == "openvpn"


def test_an_upload_with_no_config_is_refused():
    with pytest.raises(ValueError, match="Nothing in the upload"):
        vpn.parse_bundle("readme.zip", _zip({"readme.txt": "hi"}))


def test_secrets_are_sealed_under_the_store_key_and_survive_a_save(monkeypatch):
    monkeypatch.setattr(settings, "store_key", "cluster-secret")
    (record,) = vpn.import_profiles("Office", "a.ovpn", b"client\nremote a.b 1194\nauth-user-pass\n<ca>\nx\n</ca>\n", username="u", password="p")
    assert "secret" not in record and record["sealed"]["key_id"] == filecrypt.key_id()
    assert vpn._secret(record) == {"template": "client\nauth-user-pass\n<ca>\nx\n</ca>\n", "preshared_keys": {}, "username": "u", "password": "p"}
    import asyncio

    asyncio.run(vpn.save())
    written = yaml.safe_load(open(settings.vpn_profiles_path, encoding="utf-8"))
    assert "auth-user-pass" not in yaml.safe_dump(written) and "u\n" not in yaml.safe_dump(written)
    state.vpn_profiles.clear()
    vpn.load()
    assert vpn.readable(state.vpn_profiles[record["id"]])
    monkeypatch.setattr(settings, "store_key", "another-secret")
    assert not vpn.readable(state.vpn_profiles[record["id"]])
    with pytest.raises(vpn.Unusable):
        vpn.render(state.vpn_profiles[record["id"]], record["endpoints"][0], "10.230.0.2")


def _user(username="alice", groups=(), vpn_allowed=True, admin=False):
    return {"username": username, "groups": list(groups), "is_admin": admin, "effective_settings": {"vpn": vpn_allowed, "groups": list(groups)}}


def test_users_are_offered_the_profiles_open_to_them_and_the_endpoints_published(monkeypatch):
    monkeypatch.setattr(vpn, "_on_docker", lambda: True)
    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1", "de1", "fr1")), username="u", password="p", users=["bob"], groups=["staff"])
    ids = [e["id"] for e in record["endpoints"]]
    assert len(ids) == 6
    assert vpn.choices_for(_user("alice")) == []
    assert [c.name for c in vpn.choices_for(_user("bob"))] == ["Nord"]
    assert [c.name for c in vpn.choices_for(_user("carol", groups=["staff"]))] == ["Nord"]
    # Nothing published offers the first endpoint alone; publishing orders the offer.
    assert [e.id for e in vpn.choices_for(_user("bob"))[0].endpoints] == ids[:1]
    vpn.update_profile(record, {"published": [ids[2], ids[0]]})
    assert [e.id for e in vpn.choices_for(_user("bob"))[0].endpoints] == [ids[2], ids[0]]
    assert vpn.choices_for(_user("bob", vpn_allowed=False)) == []


def test_choose_resolves_a_launch_to_a_profile_and_an_endpoint():
    from fastapi import HTTPException

    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1", "de1")), username="u", password="p", users=["bob"], groups=[])
    ids = [e["id"] for e in record["endpoints"]]
    assert vpn.choose(None, None, _user("bob"), _user("bob")["effective_settings"]) is None
    chosen = vpn.choose(record["id"], None, _user("bob"), _user("bob")["effective_settings"])
    assert chosen[1]["id"] == ids[0]
    vpn.update_profile(record, {"published": [ids[1]]})
    with pytest.raises(HTTPException) as refused:
        vpn.choose(record["id"], ids[0], _user("bob"), _user("bob")["effective_settings"])
    assert refused.value.status_code == 404
    with pytest.raises(HTTPException) as refused:
        vpn.choose(record["id"], None, _user("alice"), _user("alice")["effective_settings"])
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException) as refused:
        vpn.choose(record["id"], None, _user("bob", vpn_allowed=False), {"vpn": False})
    assert refused.value.status_code == 403
    assert vpn.choose(record["id"], None, _user("admin", admin=True), _user("admin")["effective_settings"])[1]["id"] == ids[1]


def test_update_changes_what_was_given_and_refuses_unknown_endpoints():
    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="u", password="p")
    endpoint_id = record["endpoints"][0]["id"]
    vpn.update_profile(record, {"name": "Work", "max_tunnels": 3, "labels": {endpoint_id: "Chicago"}, "username": "u2", "password": "p2"})
    assert record["name"] == "Work" and record["max_tunnels"] == 3 and record["revision"] == 2
    assert record["endpoints"][0]["label"] == "Chicago"
    assert vpn._secret(record)["password"] == "p2"
    with pytest.raises(ValueError, match="Unknown endpoint"):
        vpn.update_profile(record, {"published": ["nope"]})
    assert vpn.view(record).has_credentials and vpn.view(record).needs_credentials


def test_endpoints_are_edited_added_and_removed_in_place():
    (record,) = vpn.import_profiles("Mullvad", "a.conf", (WG_INTERFACE + "[Peer]\nPublicKey = p1\nPresharedKey = psk1\nEndpoint = 1.1.1.1:51820\n[Peer]\nPublicKey = p2\nEndpoint = 2.2.2.2:51820\n").encode())
    first, second = record["endpoints"]
    vpn.update_profile(record, {"published": [second["id"], first["id"]]})
    edits = [
        {"id": first["id"], "label": "Stockholm", "host": "1.1.1.1", "port": 51821, "public_key": "p1", "preshared_key": None},
        {"id": "new-1", "host": "3.3.3.3", "port": 51820, "public_key": "p3", "preshared_key": "psk3", "label": ""},
    ]
    # A new endpoint is published in the same save under the id the editor gave it.
    vpn.update_profile(record, {"endpoints": edits, "published": ["new-1", first["id"]]})
    kept, new = record["endpoints"]
    assert kept["id"] == first["id"] and kept["label"] == "Stockholm" and kept["port"] == 51821
    assert new["label"] == "3.3.3.3" and new["public_key"] == "p3" and new["proto"] == "udp" and new["id"] != "new-1"
    assert vpn._secret(record)["preshared_keys"] == {first["id"]: "psk1", new["id"]: "psk3"}
    assert record["published"] == [new["id"], first["id"]]
    assert [e.has_preshared_key for e in vpn.view(record).endpoints] == [True, True]
    vpn.update_profile(record, {"endpoints": [{"id": first["id"], "host": "1.1.1.1", "port": 51821, "public_key": "p1", "preshared_key": ""}]})
    assert vpn._secret(record)["preshared_keys"] == {}
    with pytest.raises(ValueError, match="public key"):
        vpn.update_profile(record, {"endpoints": [{"host": "h", "port": 1}]})


def test_endpoints_are_added_from_another_upload_of_the_same_kind():
    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="u", password="p")
    added = vpn.add_endpoints(record, "more.zip", _nord_zip(("us1", "de1")))
    assert added == 2 and sorted(e["label"] for e in record["endpoints"]) == ["de1.example.com:1194/udp", "de1.example.com:443/tcp", "us1.example.com:1194/udp", "us1.example.com:443/tcp"]
    with pytest.raises(ValueError, match="not an OpenVPN config"):
        vpn.add_endpoints(record, "a.conf", (WG_INTERFACE + "[Peer]\nPublicKey = p\nEndpoint = h:1\n").encode())


def test_connections_are_counted_across_the_deployment(monkeypatch):
    from app import cluster

    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="u", password="p")
    tcp, udp = (e["id"] for e in record["endpoints"])
    monkeypatch.setattr(vpn, "_on_docker", lambda: False)
    monkeypatch.setattr(cluster, "members", lambda *a, **k: [cluster.NODE_ID, "n97", "n98"])
    reports = {
        "n97": {"name": "n97", "sessions": [{"vpn": {"profile": record["id"], "endpoint": udp}}, {"vpn": {"profile": record["id"], "endpoint": udp}}, {"vpn": None}]},
        "n98": {"name": "n98", "sessions": [{"vpn": {"profile": record["id"], "endpoint": tcp}}, {"vpn": {"profile": "other", "endpoint": tcp}}]},
    }
    monkeypatch.setattr(cluster, "status_of", lambda node_id: reports.get(node_id))
    tunnels = vpn.tunnels_sync(record["id"])
    assert [(t.node, t.endpoint, t.sessions, t.state) for t in tunnels] == [("n97", udp, 2, "running"), ("n98", tcp, 1, "running")]


def test_the_gateway_files_of_each_kind():
    (ovpn,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="svc", password="pw")
    udp = next(e for e in ovpn["endpoints"] if e["proto"] == "udp")
    files = vpn.render(ovpn, udp, "10.230.0.2")
    assert files["env"] == "KIND=openvpn\nENDPOINT_HOST=us1.example.com\nENDPOINT_PORT=1194\nENDPOINT_PROTO=udp\nDNS_UPSTREAM=''\nWG_ADDRESS=''\nWG_MTU=''\n"
    assert files["auth.txt"] == "svc\npw\n" and files["resolv.conf"] == "nameserver 10.230.0.2\n"
    assert files["client.ovpn"].startswith("client\ndev tun\n")

    (wg,) = vpn.import_profiles("Mullvad", "a.conf", (WG_INTERFACE + "MTU = 1380\n[Peer]\nPublicKey = peer1\nPresharedKey = psk1\nEndpoint = 185.1.1.1:51820\n").encode())
    files = vpn.render(wg, wg["endpoints"][0], "10.230.1.2")
    assert "WG_ADDRESS=10.64.0.5/32\nWG_MTU=1380\n" in files["env"] and "DNS_UPSTREAM=10.64.0.1\n" in files["env"]
    assert files["wg0.conf"] == (
        "[Interface]\nPrivateKey = cGVlci1wcml2YXRlLWtleS1wZWVyLXByaXZhdGUta2V5MQ=\n\n"
        "[Peer]\nPublicKey = peer1\nPresharedKey = psk1\nEndpoint = 185.1.1.1:51820\nAllowedIPs = 0.0.0.0/0\nPersistentKeepalive = 25\n"
    )
    assert "auth.txt" not in files


class _Network:
    def __init__(self, subnet):
        self.attrs = {"IPAM": {"Config": [{"Subnet": subnet}]}}


class _Client:
    def __init__(self, subnets):
        self.networks = self

    def list(self):
        return [_Network(s) for s in self._subnets]


def test_a_gateway_network_takes_the_first_free_slice_of_the_pool(monkeypatch):
    client = _Client.__new__(_Client)
    client.networks = client
    client._subnets = ["172.17.0.0/16", "10.230.0.0/24", "10.230.1.0/24"]
    assert str(vpn._free_subnet(client)) == "10.230.2.0/24"
    monkeypatch.setattr(settings, "vpn_subnets", "10.230.0.0/23")
    with pytest.raises(RuntimeError, match="No free /24"):
        vpn._free_subnet(client)


def test_a_launch_without_docker_offers_no_vpn(monkeypatch):
    monkeypatch.setattr(vpn, "_on_docker", lambda: False)
    vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="u", password="p")
    assert vpn.choices_for(_user("bob")) == []


def test_script_directives_of_an_openvpn_config_are_dropped(caplog):
    text = "client\nremote a.b 1194\nup /tmp/x.sh\nplugin foo.so\nscript-security 2\nroute-up 'sh -c id'\ncipher AES-256-GCM\n<ca>\nx\n</ca>\n"
    with caplog.at_level("WARNING", logger="app.vpn"):
        (group,) = vpn.parse_bundle("hooks.ovpn", text.encode())
    assert group.template == "client\ncipher AES-256-GCM\n<ca>\nx\n</ca>\n"
    assert "dropped up, plugin, script-security, route-up" in caplog.text


# --- Gateways against a fake Docker daemon ----------------------------------------


class _Gateway:
    """A gateway container as the daemon shows it; starting or restarting it makes it healthy."""

    def __init__(self, daemon, name, status="running", health="healthy", created="2026-10-14T00:00:00Z"):
        self.daemon, self.name, self.id, self.status = daemon, name, name, status
        self.labels = {vpn.KEY_LABEL: name.removeprefix("sealskin-vpn-")}
        self.attrs = {"State": {"Health": {"Status": health}}, "Created": created, "NetworkSettings": {"Networks": {name: {"IPAddress": "10.230.0.2"}}}}
        self.actions = []

    def reload(self):
        pass

    def start(self, action="start"):
        self.actions.append(action)
        self.status = "running"
        self.attrs["State"]["Health"]["Status"] = "healthy"

    def restart(self, timeout=None):
        self.start("restart")

    def remove(self, force=False):
        self.actions.append("remove")
        self.daemon.containers.pop(self.name, None)

    def logs(self, tail=3):
        return b""


class _Net:
    def __init__(self, daemon, name):
        self.daemon, self.name, self.containers, self.attrs = daemon, name, [], {"IPAM": {"Config": [{"Subnet": "10.230.0.0/24"}]}}

    def connect(self, container):
        pass

    def disconnect(self, container, force=False):
        pass

    def remove(self):
        self.daemon.networks.pop(self.name, None)


class _Daemon:
    """What `vpn._acquire_sync` and `_teardown_sync` ask of a Docker client."""

    def __init__(self):
        from docker.errors import NotFound

        self.containers = {}
        self.networks = {"bridge": _Net(self, "bridge")}
        self.not_found = NotFound
        daemon = self

        class Containers:
            def get(self, name):
                if name not in daemon.containers:
                    raise daemon.not_found(name)
                return daemon.containers[name]

            def list(self, all=False, filters=None):
                return [c for c in daemon.containers.values() if c.name.startswith("sealskin-vpn-")]

            def create(self, image, name, **kwargs):
                daemon.containers[name] = _Gateway(daemon, name, status="created", health=None)
                daemon.containers[name].attrs["NetworkSettings"]["Networks"][name]["IPAddress"] = kwargs["networking_config"][name]
                return daemon.containers[name]

        class Networks:
            def get(self, name):
                if name not in daemon.networks:
                    raise daemon.not_found(name)
                return daemon.networks[name]

            def list(self):
                return list(daemon.networks.values())

            def create(self, name, **kwargs):
                daemon.networks[name] = _Net(daemon, name)
                return daemon.networks[name]

        class Images:
            def get(self, image):
                return object()

        class Api:
            def create_endpoint_config(self, ipv4_address):
                return ipv4_address

        self.containers_api, self.networks_api, self.images, self.api = Containers(), Networks(), Images(), Api()

    @property
    def client(self):
        daemon = self

        class Client:
            containers = daemon.containers_api
            networks = daemon.networks_api
            images = daemon.images
            api = daemon.api

        return Client()


@pytest.fixture
def daemon(monkeypatch):
    fake = _Daemon()
    monkeypatch.setattr(vpn, "_client", lambda: fake.client)
    monkeypatch.setattr(vpn, "_on_docker", lambda: True)
    monkeypatch.setattr(vpn, "_on_kubernetes", lambda: False)
    monkeypatch.setattr(vpn.time, "sleep", lambda _s: None)
    vpn._in_flight.clear()
    yield fake
    vpn._in_flight.clear()


def _gateway_of(daemon, status="running", health="healthy", created="2026-10-14T00:00:00Z"):
    (record,) = vpn.import_profiles("Mullvad", "a.conf", (WG_INTERFACE + "[Peer]\nPublicKey = p1\nEndpoint = 1.1.1.1:51820\n").encode())
    endpoint = record["endpoints"][0]
    key = vpn.key_for(record["id"], endpoint["id"])
    name = vpn._name(key)
    daemon.containers[name] = _Gateway(daemon, name, status, health, created)
    daemon.networks[name] = _Net(daemon, name)
    return record, endpoint, key


async def test_a_launch_in_progress_holds_the_gateway_against_a_release(daemon):
    record, endpoint, key = _gateway_of(daemon)
    name = vpn._name(key)
    got = await vpn.acquire(record, endpoint, "launching")
    assert got["key"] == key and vpn.in_flight(key) == 1 and vpn.in_use(key) == 1
    # Another session of the endpoint ends while the launch has not registered its session yet.
    await vpn.release({"key": key})
    assert name in daemon.containers and name in daemon.networks
    vpn.launched(key)
    assert vpn.in_flight(key) == 0
    await vpn.release({"key": key})
    assert name not in daemon.containers and name not in daemon.networks


async def test_reconcile_leaves_a_gateway_a_launch_holds(daemon):
    record, endpoint, key = _gateway_of(daemon, created="2020-01-01T00:00:00Z")
    name = vpn._name(key)
    await vpn.acquire(record, endpoint, "launching")
    await vpn.reconcile()
    assert name in daemon.containers
    vpn.launched(key)
    await vpn.reconcile()
    assert name not in daemon.containers


async def test_a_gateway_with_sessions_is_brought_back_in_place(daemon):
    record, endpoint, key = _gateway_of(daemon, status="exited", health="unhealthy")
    name = vpn._name(key)
    state.sessions["s"] = {"vpn": {"key": key}}
    gateway = daemon.containers[name]
    got = await vpn.acquire(record, endpoint, "another")
    assert gateway.actions == ["start"] and daemon.containers[name] is gateway and name in daemon.networks
    assert got["gateway_ip"] == "10.230.0.2" and got["network"] == name
    vpn.launched(key)

    gateway.status, gateway.attrs["State"]["Health"]["Status"], gateway.actions = "running", "unhealthy", []
    await vpn.acquire(record, endpoint, "third")
    assert gateway.actions == ["restart"] and daemon.containers[name] is gateway


async def test_a_gateway_nobody_uses_is_rebuilt(daemon):
    record, endpoint, key = _gateway_of(daemon, status="exited", health="unhealthy")
    name = vpn._name(key)
    old = daemon.containers[name]
    got = await vpn.acquire(record, endpoint, "first")
    assert old.actions == ["remove"] and daemon.containers[name] is not old
    assert daemon.containers[name].actions == ["start"] and got["gateway_ip"] == "10.230.1.2"
