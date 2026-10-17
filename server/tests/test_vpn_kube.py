"""VPN hubs on Kubernetes: keys and addresses, the hub's objects, a session's spoke, and the sidecar in its pod."""

import base64
import json

import httpx
import pytest

from app import vpn, vpn_kube
from app.providers import kubernetes_provider as kp
from app.settings import settings
from app.state import state
from tests import test_kubernetes_provider as provider_tests
from tests.test_kubernetes_provider import SERVER_POD, _provider
from tests.test_vpn import WG_INTERFACE, _nord_zip

#: The provider tests' fake API server, for the session pod's manifest.
api = provider_tests.api


class HubApi:
    """The API server as the hub code uses it: pods, secrets, and services of a namespace."""

    def __init__(self):
        self.pods = {"sealskin-0": SERVER_POD}
        self.secrets = {}
        self.services = {}
        self.pod_templates = {}
        self.ready = True
        self.log = "[sealskin-vpn] traffic leaves as 1.2.3.4"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if "/podtemplates/" in path:
            template = self.pod_templates.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=template) if template else httpx.Response(404, json={"message": "not found"})
        for resource, store in (("pods", self.pods), ("secrets", self.secrets), ("services", self.services)):
            marker = f"/{resource}"
            if not (path.endswith(marker) or f"{marker}/" in path):
                continue
            if path.endswith(marker) and request.method == "POST":
                name = body["metadata"]["name"]
                if resource == "pods":
                    body["status"] = {"phase": "Running", "podIP": "10.244.1.9", "conditions": [{"type": "Ready", "status": "True" if self.ready else "False"}]}
                    body["metadata"]["creationTimestamp"] = "2026-10-14T00:00:00Z"
                if resource == "services":
                    body["spec"]["clusterIP"] = "10.96.0.50"
                store[name] = body
                return httpx.Response(201, json=body)
            if path.endswith(marker) and request.method == "GET":
                selector = dict(pair.split("=") for pair in request.url.params.get("labelSelector", "").split(",") if "=" in pair)
                items = [o for o in store.values() if selector.items() <= (o["metadata"].get("labels") or {}).items()]
                return httpx.Response(200, json={"items": items})
            name = path.split(marker + "/", 1)[1].split("/")[0]
            if path.endswith("/log"):
                return httpx.Response(200, text=self.log)
            if request.method == "DELETE":
                return httpx.Response(200 if store.pop(name, None) else 404, json={"message": "gone"})
            if name not in store:
                return httpx.Response(404, json={"message": f"{resource} {name} not found"})
            if request.method == "PATCH":
                store[name]["data"].update(body["data"])
            return httpx.Response(200, json=store[name])
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


@pytest.fixture
def hub_api(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("t0k3n")
    fake = HubApi()
    client = kp.KubeClient("https://kube.test", "team-a", token_path=str(token), transport=httpx.MockTransport(fake.handler))
    client.owner = {"apiVersion": "apps/v1", "kind": "Deployment", "name": "sealskin", "uid": "dep-uid"}
    monkeypatch.setattr(kp, "_client", client)
    monkeypatch.setattr(vpn_kube.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(vpn, "_on_kubernetes", lambda: True)
    monkeypatch.setattr(vpn, "_on_docker", lambda: False)
    monkeypatch.setattr(state, "instance_name", "sealskin")
    executed = []

    async def exec_(self, pod, container, command, timeout=30.0):
        executed.append((pod, container, command))
        return ""

    monkeypatch.setattr(kp.KubeClient, "exec", exec_)
    fake.executed = executed
    vpn._in_flight.clear()
    yield fake
    vpn._in_flight.clear()


async def _no_sleep(_seconds):
    return None


def _wg_profile():
    (record,) = vpn.import_profiles("Mullvad", "a.conf", (WG_INTERFACE + "[Peer]\nPublicKey = p1\nEndpoint = 1.1.1.1:51820\n").encode())
    return record, record["endpoints"][0]


def test_keys_and_addresses():
    private, public = vpn_kube.keypair()
    assert len(base64.b64decode(private)) == 32 and len(base64.b64decode(public)) == 32 and private != public
    assert vpn_kube.hub_address() == "10.231.0.1"
    first = vpn_kube.spoke_address("session-a", set())
    assert first.startswith("10.231.") and first not in ("10.231.0.0", "10.231.0.1")
    assert vpn_kube.spoke_address("session-a", set()) == first
    assert vpn_kube.spoke_address("session-a", {first}) != first


async def test_a_session_starts_the_hub_and_joins_it_as_a_spoke(hub_api):
    record, endpoint = _wg_profile()
    key = vpn.key_for(record["id"], endpoint["id"])
    got = await vpn.acquire(record, endpoint, "session-a")
    vpn.launched(key)
    name = f"sealskin-vpn-sealskin-{key}"
    assert set(hub_api.pods) == {"sealskin-0", name} and name in hub_api.secrets and name in hub_api.services
    pod = hub_api.pods[name]
    gateway = pod["spec"]["containers"][0]
    assert gateway["args"] == ["hub"] and gateway["securityContext"] == {"capabilities": {"add": ["NET_ADMIN"]}}
    assert pod["metadata"]["ownerReferences"] == [kp.kube().owner]
    assert pod["spec"]["volumes"][1]["hostPath"] == {"path": "/dev/net/tun", "type": "CharDevice"}
    files = {k: base64.b64decode(v).decode() for k, v in hub_api.secrets[name]["data"].items()}
    assert "HUB_ADDRESS=10.231.0.1/16\nHUB_PORT=51820\nLOCAL_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16\n" in files["env"]
    assert files["hub.pub"].strip() == got["spoke"]["HUB_PUBLIC_KEY"] and "PrivateKey = cGVl" in files["wg0.conf"]
    assert got["spoke"]["HUB_ENDPOINT"] == "10.96.0.50:51820" and got["spoke"]["SPOKE_ADDRESS"] == f"{got['spoke_address']}/32"
    assert files["peers"] == f"{got['spoke_public_key']} {got['spoke_address']}/32\n"
    assert hub_api.executed == [(name, "gateway", ["wg", "set", "hub0", "peer", got["spoke_public_key"], "allowed-ips", f"{got['spoke_address']}/32"])]
    assert hub_api.services[name]["spec"]["selector"] == {vpn.KEY_LABEL: key, "app.kubernetes.io/instance": "sealskin"}

    # A second session shares the hub and takes an address of its own.
    state.sessions["s-a"] = {"vpn": {"key": key, "spoke_public_key": got["spoke_public_key"], "spoke_address": got["spoke_address"]}}
    second = await vpn.acquire(record, endpoint, "session-b")
    vpn.launched(key)
    assert len(hub_api.pods) == 2 and second["spoke_address"] != got["spoke_address"]
    assert len(hub_api.executed) == 2
    tunnels = await vpn.tunnels(record["id"])
    assert [(t.endpoint, t.sessions, t.state) for t in tunnels] == [(endpoint["id"], 1, "healthy")]

    # The last session gone takes the hub down; the first only its spoke.
    await vpn.release(second)
    assert name in hub_api.pods and hub_api.executed[-1][2] == ["wg", "set", "hub0", "peer", second["spoke_public_key"], "remove"]
    del state.sessions["s-a"]
    await vpn.release(got)
    assert name not in hub_api.pods and name not in hub_api.services and name not in hub_api.secrets


async def test_a_hub_that_does_not_come_up_is_taken_down_with_its_last_words(hub_api):
    hub_api.ready = False
    hub_api.log = "[sealskin-vpn] cannot resolve vpn.example.com"
    monkey = vpn_kube.READY_TIMEOUT
    vpn_kube.READY_TIMEOUT = 0
    try:
        record, endpoint = _wg_profile()
        with pytest.raises(Exception) as refused:
            await vpn.acquire(record, endpoint, "session-a")
    finally:
        vpn_kube.READY_TIMEOUT = monkey
    assert refused.value.status_code == 502 and "cannot resolve vpn.example.com" in refused.value.detail
    assert len(hub_api.pods) == 1 and not hub_api.secrets and not hub_api.services


async def test_the_cap_counts_hubs_across_the_deployment(hub_api, monkeypatch):
    (record,) = vpn.import_profiles("Nord", "ovpn.zip", _nord_zip(("us1",)), username="u", password="p")
    tcp, udp = record["endpoints"]
    vpn.update_profile(record, {"max_tunnels": 1})
    await vpn.acquire(record, tcp, "session-a")
    with pytest.raises(Exception) as refused:
        await vpn.acquire(record, udp, "session-b")
    assert refused.value.status_code == 409 and "1 of its 1 connections" in refused.value.detail


def test_hub_names_carry_the_server_instance_within_a_dns_label(monkeypatch):
    monkeypatch.setattr(state, "instance_name", "sealskin")
    assert vpn_kube._name("abc123") == "sealskin-vpn-sealskin-abc123"
    monkeypatch.setattr(state, "instance_name", "Team_A.sealskin")
    assert vpn_kube._name("abc123") == "sealskin-vpn-team-a-sealskin-abc123"
    monkeypatch.setattr(state, "instance_name", "a-very-long-deployment-name-of-a-sealskin-server")
    name = vpn_kube._name("abc123def456")
    assert name.startswith("sealskin-vpn-a-very-long-deplo-") and name.endswith("-abc123def456") and len(name) <= 63
    monkeypatch.setattr(state, "instance_name", "")
    assert vpn_kube._name("abc123") == "sealskin-vpn-abc123"


async def test_another_servers_hub_under_the_name_is_refused_and_left_alone(hub_api):
    record, endpoint = _wg_profile()
    key = vpn.key_for(record["id"], endpoint["id"])
    name = vpn_kube._name(key)
    hub_api.pods[name] = {"metadata": {"name": name, "labels": {vpn.KEY_LABEL: key, "app.kubernetes.io/instance": "other"}}, "status": {"phase": "Failed"}}
    with pytest.raises(Exception) as refused:
        await vpn.acquire(record, endpoint, "session-a")
    assert refused.value.status_code == 409 and "not this server's" in refused.value.detail
    assert name in hub_api.pods and not hub_api.secrets
    await vpn.release({"key": key})
    assert name in hub_api.pods


async def test_a_failed_hub_pod_comes_back_over_its_secret(hub_api):
    record, endpoint = _wg_profile()
    key = vpn.key_for(record["id"], endpoint["id"])
    name = vpn_kube._name(key)
    got = await vpn.acquire(record, endpoint, "session-a")
    vpn.launched(key)
    state.sessions["s-a"] = {"vpn": {"key": key, "profile": record["id"], "endpoint": endpoint["id"], **{k: got[k] for k in ("spoke_public_key", "spoke_address")}}}
    secret_before = dict(hub_api.secrets[name]["data"])
    service_before = hub_api.services[name]

    # Evicted: reconcile creates the pod again without waiting, the Secret and Service as they were.
    hub_api.pods[name]["status"]["phase"] = "Failed"
    failed = hub_api.pods[name]
    await vpn.reconcile()
    assert hub_api.pods[name] is not failed and hub_api.pods[name]["status"]["phase"] == "Running"
    assert hub_api.secrets[name]["data"] == secret_before and hub_api.services[name] is service_before

    # Gone altogether: the same, as long as a session leaves through it.
    del hub_api.pods[name]
    await vpn.reconcile()
    assert name in hub_api.pods and hub_api.secrets[name]["data"] == secret_before

    # A launch on a failed hub brings it back the same way, and the new spoke joins the old one.
    hub_api.pods[name]["status"]["phase"] = "Failed"
    second = await vpn.acquire(record, endpoint, "session-b")
    assert hub_api.pods[name]["status"]["phase"] == "Running" and second["spoke"]["HUB_PUBLIC_KEY"] == got["spoke"]["HUB_PUBLIC_KEY"]
    peers = base64.b64decode(hub_api.secrets[name]["data"]["peers"]).decode()
    assert got["spoke_public_key"] in peers and second["spoke_public_key"] in peers
    assert hub_api.secrets[name]["data"]["hub.key"] == secret_before["hub.key"]

    # With the profile gone, reconcile leaves what is there.
    state.vpn_profiles.clear()
    hub_api.pods[name]["status"]["phase"] = "Failed"
    await vpn.reconcile()
    assert hub_api.pods[name]["status"]["phase"] == "Failed" and name in hub_api.secrets


async def test_the_hub_pod_takes_its_placement_from_the_session_template(hub_api):
    hub_api.pod_templates["sealskin-session"] = {
        "template": {
            "spec": {
                "imagePullSecrets": [{"name": "registry"}],
                "tolerations": [{"key": "vpn", "operator": "Exists"}],
                "nodeSelector": {"egress": "true"},
                "runtimeClassName": "kata",
                "containers": [{"name": "app", "image": "x"}],
            }
        }
    }
    record, endpoint = _wg_profile()
    await vpn.acquire(record, endpoint, "session-a")
    pod = hub_api.pods[vpn_kube._name(vpn.key_for(record["id"], endpoint["id"]))]
    assert pod["spec"]["imagePullSecrets"] == [{"name": "registry"}]
    assert pod["spec"]["tolerations"] == [{"key": "vpn", "operator": "Exists"}]
    assert pod["spec"]["nodeSelector"] == {"egress": "true"}
    assert "runtimeClassName" not in pod["spec"] and [c["name"] for c in pod["spec"]["containers"]] == ["gateway"]


def test_the_session_pod_gets_the_spoke_sidecar_and_resolves_at_the_hub(api):
    spoke = {"SPOKE_PRIVATE_KEY": "k", "SPOKE_ADDRESS": "10.231.4.5/32", "HUB_PUBLIC_KEY": "h", "HUB_ENDPOINT": "10.96.0.50:51820", "HUB_ADDRESS": "10.231.0.1", "LOCAL_CIDRS": "10.0.0.0/8"}
    manifest = _provider()._pod_manifest("sess", {}, {}, None, None, {"key": "k", "spoke": spoke})
    (sidecar,) = manifest["spec"]["initContainers"]
    assert sidecar["name"] == "vpn" and sidecar["image"] == settings.vpn_image and sidecar["args"] == ["spoke"]
    assert sidecar["restartPolicy"] == "Always" and sidecar["securityContext"] == {"capabilities": {"add": ["NET_ADMIN"]}}
    assert {e["name"]: e["value"] for e in sidecar["env"]} == spoke
    assert sidecar["startupProbe"]["exec"]["command"] == ["/usr/local/bin/sealskin-vpn", "health"]
    assert manifest["spec"]["dnsPolicy"] == "None" and manifest["spec"]["dnsConfig"] == {"nameservers": ["10.231.0.1"]}
    assert manifest["spec"]["containers"][0]["name"] == "app" and "securityContext" not in manifest["spec"]["containers"][0]
    plain = _provider()._pod_manifest("sess", {}, {}, None, None)
    assert "initContainers" not in plain["spec"] and "dnsPolicy" not in plain["spec"]
