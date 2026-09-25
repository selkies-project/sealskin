"""Kubernetes provider against an in-memory API server."""

import json

import httpx
import pytest
from fastapi import HTTPException

from app.providers import kubernetes_provider as kp
from app.settings import settings
from app.state import state

SERVER_POD = {
    "metadata": {
        "name": "sealskin-0",
        "uid": "pod-uid",
        "labels": {"app.kubernetes.io/name": "sealskin", "pod-template-hash": "5d4f"},
        "ownerReferences": [
            {"apiVersion": "apps/v1", "kind": "ReplicaSet", "name": "sealskin-5d4f", "uid": "rs-uid", "controller": True}
        ],
    },
    "spec": {
        "nodeName": "node-a",
        "containers": [
            {
                "name": "sealskin",
                "volumeMounts": [
                    {"name": "config", "mountPath": "/config"},
                    {"name": "storage", "mountPath": "/storage/", "subPath": "data"},
                    {"name": "token", "mountPath": "/var/run/secrets/kubernetes.io/serviceaccount"},
                ],
            }
        ],
        "volumes": [
            {"name": "config", "persistentVolumeClaim": {"claimName": "config"}},
            {"name": "storage", "persistentVolumeClaim": {"claimName": "storage"}},
            {"name": "token", "projected": {"sources": []}},
        ],
    },
}


class FakeApi:
    """Answers the REST calls the provider makes."""

    def __init__(self):
        self.pods = {"sealskin-0": SERVER_POD}
        self.posted = []
        self.deleted = []
        self.pvc_modes = {"config": ["ReadWriteOnce"], "storage": ["ReadWriteMany"]}
        self.nodes = []
        self.quotas = []
        self.nodes_forbidden = False
        self.device_classes = None
        self.reject_runtime_class = False
        self.pod_status = {"phase": "Running", "podIP": "fd00::5"}
        self.pod_templates = {}
        self.events = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/nodes":
            if self.nodes_forbidden:
                return httpx.Response(403, json={"message": "nodes is forbidden"})
            return httpx.Response(200, json={"items": self.nodes})
        if path.endswith("/podtemplates"):
            selector = request.url.params.get("labelSelector", "")
            items = [{**t, "metadata": {**t.get("metadata", {}), "name": n}} for n, t in self.pod_templates.items()]
            return httpx.Response(200, json={"items": [i for i in items if selector in i["metadata"].get("labels", {})]})
        if path == "/apis/resource.k8s.io/v1/deviceclasses":
            if self.device_classes is None:
                return httpx.Response(404, json={"message": "the server could not find the requested resource"})
            return httpx.Response(200, json={"items": self.device_classes})
        if "/podtemplates/" in path:
            name = path.rsplit("/", 1)[1]
            if name not in self.pod_templates:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=self.pod_templates[name])
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": self.events})
        if path.endswith("/resourcequotas"):
            return httpx.Response(200, json={"items": self.quotas})
        if "/persistentvolumeclaims/" in path:
            claim = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"spec": {"accessModes": self.pvc_modes[claim]}})
        if "/replicasets/" in path:
            return httpx.Response(
                200,
                json={
                    "metadata": {
                        "ownerReferences": [
                            {"apiVersion": "apps/v1", "kind": "Deployment", "name": "sealskin", "uid": "dep-uid", "controller": True}
                        ]
                    }
                },
            )
        if path.endswith("/pods") and request.method == "POST":
            body = json.loads(request.content)
            if self.reject_runtime_class and body["spec"].get("runtimeClassName") == "nvidia":
                return httpx.Response(403, json={"message": 'pod rejected: RuntimeClass "nvidia" not found'})
            name = body["metadata"]["generateName"] + "x1y2z"
            body["metadata"]["name"] = name
            body["status"] = dict(self.pod_status)
            self.pods[name] = body
            self.posted.append(body)
            return httpx.Response(201, json=body)
        if path.endswith("/pods") and request.method == "GET":
            items = list(self.pods.values())
            selector = request.url.params.get("labelSelector")
            if selector:
                wanted = dict(pair.split("=") for pair in selector.split(","))
                items = [p for p in items if wanted.items() <= p["metadata"].get("labels", {}).items()]
            return httpx.Response(200, json={"items": items})
        if "/pods/" in path:
            name = path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                self.deleted.append(name)
                return httpx.Response(200 if self.pods.pop(name, None) else 404, json={"message": "gone"})
            if name not in self.pods:
                return httpx.Response(404, json={"message": f'pods "{name}" not found'})
            return httpx.Response(200, json=self.pods[name])
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


@pytest.fixture
def api(tmp_path, monkeypatch):
    token = tmp_path / "token"
    token.write_text("t0k3n")
    fake = FakeApi()
    client = kp.KubeClient(
        "https://kube.test", "team-a", token_path=str(token), transport=httpx.MockTransport(fake.handler)
    )
    monkeypatch.setattr(kp, "_client", client)
    monkeypatch.setattr(kp.socket, "gethostname", lambda: "sealskin-0")
    monkeypatch.setattr(settings, "storage_path", "/storage")
    monkeypatch.setattr(kp.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(kp, "NVIDIA_GPUS_DIR", str(tmp_path / "no-nvidia"))
    state.image_metadata.clear()
    return fake


async def _no_sleep(_seconds):
    return None


def _provider(**docker_overrides):
    return kp.KubernetesProvider(
        {"provider_config": {"image": "lscr.io/linuxserver/firefox:latest", "port": 3000, "docker_overrides": docker_overrides}}
    )


async def test_inspect_self_maps_volumes_owner_and_claims(api):
    await kp.KubernetesProvider().inspect_self()
    client = kp.kube()
    assert client.labels == {"app.kubernetes.io/name": "sealskin", "pod-template-hash": "5d4f"}
    assert client.owner == {
        "apiVersion": "apps/v1", "kind": "Deployment", "name": "sealskin", "uid": "dep-uid", "blockOwnerDeletion": False
    }
    assert state.instance_name == "sealskin"
    assert client.shared_claims == {"storage"}
    provider = kp.KubernetesProvider()
    volume, sub_path = provider._volume_for("/storage/alice/work")
    assert volume["persistentVolumeClaim"]["claimName"] == "storage" and sub_path == "data/alice/work"
    assert provider._volume_for("/var/run/secrets/kubernetes.io/serviceaccount/token") is None
    assert provider._volume_for("/elsewhere") is None


async def test_pod_manifest_maps_storage_options_and_ownership(api):
    await kp.KubernetesProvider().inspect_self()
    provider = _provider(
        shm_size="2g",
        mem_limit="4g",
        nano_cpus=1_500_000_000,
        cpu_shares=512,
        security_opt=["seccomp=unconfined", "apparmor:unconfined", "no-new-privileges"],
        cap_add=["CAP_SYS_NICE"],
        extra_hosts={"a.test": "10.0.0.1", "b.test": "10.0.0.1"},
        sysctls={"net.ipv4.ping_group_range": "0 2147483647"},
        group_add=["44", "video"],
        volumes=["/srv/models:/models:ro"],
        tmpfs={"/scratch": "size=64m"},
        ulimits=["ignored"],
    )
    pod = provider._pod_manifest(
        "11111111-2222-3333-4444-555555555555",
        {"SUBFOLDER": "/s/", "PASSWORD": "p"},
        {
            "/storage/alice/work": {"bind": "/config", "mode": "rw"},
            "/storage/alice/_sealskin_shared_files": {"bind": "/config/Desktop/files", "mode": "rw"},
        },
        None,
    )
    spec, container = pod["spec"], pod["spec"]["containers"][0]
    assert pod["metadata"]["generateName"] == "sealskin-11111111-"
    assert pod["metadata"]["labels"]["sealskin.app/session"] == "11111111-2222-3333-4444-555555555555"
    assert pod["metadata"]["labels"]["app.kubernetes.io/instance"] == "sealskin"
    assert pod["metadata"]["ownerReferences"][0]["kind"] == "Deployment"
    assert spec["automountServiceAccountToken"] is False and spec["enableServiceLinks"] is False
    assert "affinity" not in spec
    volumes = {v["name"]: v for v in spec["volumes"]}
    mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
    assert mounts["/config"]["subPath"] == "data/alice/work"
    assert mounts["/config/Desktop/files"]["subPath"] == "data/alice/_sealskin_shared_files"
    assert mounts["/config/Desktop/files"]["name"] == mounts["/config"]["name"]
    assert [v for v in spec["volumes"] if "persistentVolumeClaim" in v] == [
        {"name": mounts["/config"]["name"], "persistentVolumeClaim": {"claimName": "storage"}}
    ]
    assert volumes[mounts["/config"]["name"]]["persistentVolumeClaim"] == {"claimName": "storage"}
    assert volumes[mounts["/dev/shm"]["name"]]["emptyDir"] == {"medium": "Memory", "sizeLimit": "2Gi"}
    assert volumes[mounts["/scratch"]["name"]]["emptyDir"] == {"medium": "Memory", "sizeLimit": "64Mi"}
    assert mounts["/models"]["readOnly"] is True and volumes[mounts["/models"]["name"]]["hostPath"] == {"path": "/srv/models"}
    assert container["resources"] == {"limits": {"memory": "4Gi", "cpu": "1500m"}, "requests": {"cpu": "500m"}}
    assert container["securityContext"] == {
        "seccompProfile": {"type": "Unconfined"},
        "appArmorProfile": {"type": "Unconfined"},
        "allowPrivilegeEscalation": False,
        "capabilities": {"add": ["SYS_NICE"]},
    }
    assert pod["metadata"]["annotations"] == {"container.apparmor.security.beta.kubernetes.io/app": "unconfined"}
    assert spec["hostAliases"] == [{"ip": "10.0.0.1", "hostnames": ["a.test", "b.test"]}]
    assert spec["securityContext"] == {
        "supplementalGroups": [44],
        "sysctls": [{"name": "net.ipv4.ping_group_range", "value": "0 2147483647"}],
    }
    assert {"name": "SUBFOLDER", "value": "/s/"} in container["env"]
    assert container["ports"] == [{"name": "http", "containerPort": 3000}]


async def test_node_bound_storage_pins_sessions_to_the_server_node(api):
    api.pvc_modes["storage"] = ["ReadWriteOnce"]
    await kp.KubernetesProvider().inspect_self()
    pod = _provider()._pod_manifest("s" * 36, {}, {"/storage/bob": {"bind": "/config"}}, None)
    assert pod["spec"]["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"] == [
        {
            "labelSelector": {"matchLabels": {"app.kubernetes.io/name": "sealskin", "pod-template-hash": "5d4f"}},
            "topologyKey": "kubernetes.io/hostname",
        }
    ]
    with pytest.raises(HTTPException):
        _provider()._pod_manifest("s" * 36, {}, {"/tmp/x": {"bind": "/config"}}, None)


async def test_gpu_session_requests_the_resource(api):
    await kp.KubernetesProvider().inspect_self()
    gpu = {
        "device": "nvidia.com/gpu/NVIDIA-L4",
        "type": "nvidia",
        "resource": "nvidia.com/gpu",
        "node_selector": {"nvidia.com/gpu.product": "NVIDIA-L4"},
    }
    pod = _provider()._pod_manifest("s" * 36, {}, {}, gpu)
    spec, container = pod["spec"], pod["spec"]["containers"][0]
    assert container["resources"]["limits"] == {"nvidia.com/gpu": "1"}
    assert spec["tolerations"] == [{"key": "nvidia.com/gpu", "operator": "Exists"}]
    mig = {**gpu, "resource": "nvidia.com/mig-1g.10gb", "node_selector": {}}
    assert _provider()._pod_manifest("s" * 36, {}, {}, mig)["spec"]["tolerations"] == [
        {"key": "nvidia.com/mig-1g.10gb", "operator": "Exists"},
        {"key": "nvidia.com/gpu", "operator": "Exists"},
    ]
    assert spec["nodeSelector"] == {"kubernetes.io/os": "linux", "nvidia.com/gpu.product": "NVIDIA-L4"}
    assert spec["runtimeClassName"] == "nvidia"
    assert {"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "all"} in container["env"]


async def test_launch_waits_for_the_pod_and_falls_back_without_runtime_class(api, monkeypatch):
    await kp.KubernetesProvider().inspect_self()
    api.reject_runtime_class = True
    readiness = {}

    async def ready(self, session_id, current_ip, env_vars, **kwargs):
        readiness["ip"] = await current_ip()
        return readiness["ip"]

    monkeypatch.setattr(kp.KubernetesProvider, "_wait_until_ready", ready)
    gpu = {"device": "nvidia.com/gpu", "type": "nvidia", "resource": "nvidia.com/gpu", "node_selector": {}}
    result = await _provider().launch("s" * 36, {"SUBFOLDER": "/s/"}, {}, gpu)
    assert result == {"instance_id": "sealskin-ssssssss-x1y2z", "ip": "fd00::5", "port": 3000}
    assert readiness["ip"] == "fd00::5"
    assert "runtimeClassName" not in api.posted[0]["spec"]


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        ({"phase": "Failed", "containerStatuses": [{"state": {"terminated": {"reason": "OOMKilled"}}}]}, "OOMKilled"),
        ({"phase": "Pending", "containerStatuses": [{"state": {"waiting": {"reason": "InvalidImageName"}}}]}, "InvalidImageName"),
        (
            {"phase": "Pending", "containerStatuses": [{"state": {"waiting": {"reason": "ImagePullBackOff", "message": "manifest unknown"}}}]},
            "manifest unknown",
        ),
    ],
)
async def test_launch_failures_delete_the_pod(api, monkeypatch, status, detail):
    await kp.KubernetesProvider().inspect_self()
    api.pod_status = status
    monkeypatch.setattr(kp, "PULL_FAILURE_GRACE", -1)
    with pytest.raises(HTTPException) as excinfo:
        await _provider().launch("s" * 36, {}, {}, None)
    assert detail in excinfo.value.detail
    assert api.deleted == ["sealskin-ssssssss-x1y2z"]


async def test_running_state_and_managed_instances(api):
    await kp.KubernetesProvider().inspect_self()
    provider = _provider()
    api.pods["done"] = {"metadata": {"name": "done"}, "status": {"phase": "Succeeded"}}
    api.pods["leaving"] = {"metadata": {"name": "leaving", "deletionTimestamp": "2026-01-01T00:00:00Z"}, "status": {"phase": "Running"}}
    api.pods["mine"] = {
        "metadata": {
            "name": "mine",
            "creationTimestamp": "2026-09-26T00:00:00Z",
            "labels": {"app.kubernetes.io/managed-by": "sealskin", "app.kubernetes.io/instance": "sealskin"},
        },
        "status": {"phase": "Running"},
    }
    assert await provider.is_running("mine") is True
    assert await provider.is_running("done") is False
    assert await provider.is_running("leaving") is False
    assert await provider.is_running("missing") is False
    assert await provider.managed_instances() == {"mine": kp.epoch("2026-09-26T00:00:00Z")}
    await provider.stop("mine")
    await provider.stop("missing")
    assert api.deleted == ["mine", "missing"]


async def test_detect_gpus_from_nodes_and_quota(api):
    await kp.KubernetesProvider().inspect_self()
    api.nodes = [
        {
            "metadata": {"labels": {"nvidia.com/gpu.product": "NVIDIA-L4"}},
            "status": {"allocatable": {"nvidia.com/gpu": "4", "cpu": "32"}},
        },
        {"metadata": {"labels": {}}, "status": {"allocatable": {"gpu.intel.com/i915": "1", "gpu.intel.com/millicores": "1000"}}},
        {"metadata": {"labels": {"nvidia.com/gpu.workload.config": "vm-passthrough"}}, "status": {"allocatable": {"nvidia.com/pgpu": "1"}}},
        {"metadata": {"labels": {}}, "spec": {"unschedulable": True}, "status": {"allocatable": {"amd.com/gpu": "1"}}},
        {"metadata": {"labels": {}}, "status": {"allocatable": {"nvidia.com/h100": "8"}}},
        # GPUs and their audio functions bound for virtual machines, on a node without the operator's label
        {
            "metadata": {"labels": {"nvidia.com/gpu.product": "NVIDIA-A10"}},
            "status": {
                "allocatable": {
                    "nvidia.com/GA102GL_A10": "2",
                    "nvidia.com/AD102_HIGH_DEFINITION_AUDIO_CONTROLLER": "1",
                    "nvidia.com/NVIDIA_A10-12Q": "4",
                    "nvidia.com/mig-1g.10gb.me": "7",
                }
            },
        },
        {"metadata": {"labels": {"amd.com/gpu.product-name": "AMD_Radeon_PRO_W7900"}}, "status": {"allocatable": {"amd.com/gpu": "2", "amd.com/cpx_nps1": "8"}}},
        {"metadata": {"labels": {"gpu.intel.com/product": "Arc_B580"}}, "status": {"allocatable": {"gpu.intel.com/xe": "1", "gpu.intel.com/monitoring": "1"}}},
    ]
    api.quotas = [{"spec": {"hard": {"requests.nvidia.com/h100": "0"}}}]
    await kp.KubernetesProvider().detect_gpus()
    assert [(g["device"], g["type"], g["driver"]) for g in state.available_gpus] == [
        ("amd.com/gpu/AMD_Radeon_PRO_W7900", "dri3", "amdgpu"),
        ("gpu.intel.com/i915", "dri3", "i915"),
        ("gpu.intel.com/xe/Arc_B580", "dri3", "xe"),
        ("nvidia.com/gpu/NVIDIA-L4", "nvidia", "nvidia"),
        ("nvidia.com/mig-1g.10gb.me/NVIDIA-A10", "nvidia", "nvidia"),
    ]
    assert state.available_gpus[0]["node_selector"] == {"amd.com/gpu.product-name": "AMD_Radeon_PRO_W7900"}
    assert state.available_gpus[2]["node_selector"] == {"gpu.intel.com/product": "Arc_B580"}
    # A DRA device class that pods request as an extended resource.
    api.nodes = []
    api.device_classes = [
        {"metadata": {"name": "gpu.nvidia.com"}, "spec": {"extendedResourceName": "nvidia.com/gpu"}},
        {"metadata": {"name": "gpu.intel.com"}, "spec": {}},
    ]
    await kp.KubernetesProvider().detect_gpus()
    assert [g["device"] for g in state.available_gpus] == ["nvidia.com/gpu"]


async def test_detect_gpus_falls_back_to_this_node(api, monkeypatch, tmp_path):
    await kp.KubernetesProvider().inspect_self()
    api.nodes_forbidden = True
    monkeypatch.setattr(
        kp,
        "scan_render_nodes",
        lambda: [
            {"device": "/dev/dri/renderD128", "driver": "nvidia", "type": "nvidia", "index": 0},
            {"device": "/dev/dri/renderD129", "driver": "simpledrm", "type": "dri3"},
        ],
    )
    await kp.KubernetesProvider().detect_gpus()
    assert [g["device"] for g in state.available_gpus] == ["nvidia.com/gpu"]
    monkeypatch.setattr(kp, "scan_render_nodes", lambda: [])
    await kp.KubernetesProvider().detect_gpus()
    assert state.available_gpus == []
    nvidia = tmp_path / "gpus"
    (nvidia / "0000:06:00.0").mkdir(parents=True)
    monkeypatch.setattr(kp, "NVIDIA_GPUS_DIR", str(nvidia))
    api.quotas = [{"spec": {"hard": {"requests.nvidia.com/h100": "0", "limits.nvidia.com/h100": "0"}}}]
    await kp.KubernetesProvider().detect_gpus()
    assert [g["device"] for g in state.available_gpus] == ["nvidia.com/gpu"]


async def test_detect_gpus_from_the_quota_without_nodes(api, monkeypatch):
    await kp.KubernetesProvider().inspect_self()
    api.nodes_forbidden = True
    api.quotas = [
        {"status": {"hard": {"requests.nvidia.com/gpu": "4", "requests.nvidia.com/h100": "0", "pods": "50"}}},
        {"spec": {"hard": {"amd.com/gpu": "2"}}},
    ]
    monkeypatch.setattr(kp, "scan_render_nodes", lambda: [{"driver": "i915", "type": "dri3"}])
    await kp.KubernetesProvider().detect_gpus()
    assert [g["device"] for g in state.available_gpus] == ["amd.com/gpu", "nvidia.com/gpu"]


async def test_quota_scopes_decide_which_zero_caps_apply(api):
    await kp.KubernetesProvider().inspect_self()
    api.nodes = [
        {"metadata": {"labels": {}}, "status": {"allocatable": {"nvidia.com/h100": "8", "nvidia.com/gpu": "2"}}},
    ]
    preemptible = {"matchExpressions": [{"scopeName": "PriorityClass", "operator": "NotIn", "values": ["preemptible"]}]}
    api.quotas = [
        {"spec": {"hard": {"requests.nvidia.com/h100": "0"}, "scopeSelector": preemptible}},
        {"spec": {"hard": {"requests.nvidia.com/gpu": "0"}, "scopes": ["BestEffort"]}},
    ]
    await kp.KubernetesProvider().detect_gpus()
    assert [g["device"] for g in state.available_gpus] == ["nvidia.com/gpu"]
    api.pod_templates["sealskin-session"] = {"template": {"spec": {"priorityClassName": "preemptible", "containers": []}}}
    await kp.KubernetesProvider().detect_gpus()
    assert [g["device"] for g in state.available_gpus] == ["nvidia.com/gpu", "nvidia.com/h100"]
    assert kp._quota_applies({"spec": {"scopes": ["NotBestEffort", "NotTerminating"]}}, {})
    assert not kp._quota_applies({"spec": {"scopes": ["Terminating"]}}, {})
    assert kp._quota_applies({"spec": {"scopes": ["Terminating"]}}, {"activeDeadlineSeconds": 60})
    exists = {"scopeSelector": {"matchExpressions": [{"scopeName": "PriorityClass", "operator": "Exists"}]}}
    assert not kp._quota_applies({"spec": exists}, {}) and kp._quota_applies({"spec": exists}, {"priorityClassName": "a"})


async def test_gpu_options_from_pod_templates(api, monkeypatch):
    await kp.KubernetesProvider().inspect_self()
    api.nodes = [{"metadata": {"labels": {}}, "status": {"allocatable": {"nvidia.com/gpu": "4"}}}]
    api.pod_templates["sealskin-session"] = {
        "template": {"spec": {"runtimeClassName": "kata-qemu", "containers": [{"name": "app", "image": "x"}]}}
    }
    api.pod_templates["radeon"] = {
        "metadata": {"labels": {"sealskin.app/gpu": "amdgpu"}},
        "template": {
            "spec": {
                "nodeSelector": {"amd.com/gpu.product-name": "AMD_Radeon_PRO_W7900"},
                "containers": [{"name": "app", "image": "x", "resources": {"limits": {"amd.com/gpu": "1"}}}],
            }
        },
    }
    api.pod_templates["l40-dra"] = {
        "metadata": {"labels": {"sealskin.app/gpu": "nvidia"}},
        "template": {
            "spec": {
                "resourceClaims": [{"name": "gpu", "resourceClaimTemplateName": "l40"}],
                "containers": [{"name": "app", "image": "x", "resources": {"claims": [{"name": "gpu"}]}}],
            }
        },
    }
    await kp.KubernetesProvider().detect_gpus()
    assert [(g["device"], g["driver"], g["type"]) for g in state.available_gpus] == [
        ("l40-dra", "nvidia", "nvidia"),
        ("radeon", "amdgpu", "dri3"),
    ]
    monkeypatch.setattr(kp.KubernetesProvider, "_wait_until_ready", _ready)
    await _provider().launch("s" * 36, {}, {}, state.available_gpus[0])
    await _provider().launch("t" * 36, {}, {}, state.available_gpus[1])
    dra, radeon = api.posted
    app = dra["spec"]["containers"][0]
    assert dra["spec"]["resourceClaims"] == [{"name": "gpu", "resourceClaimTemplateName": "l40"}]
    assert app["resources"] == {"claims": [{"name": "gpu"}]} and app["image"] == "lscr.io/linuxserver/firefox:latest"
    # The session template's runtime class stands; the NVIDIA capabilities still apply.
    assert dra["spec"]["runtimeClassName"] == "kata-qemu"
    assert {"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "all"} in app["env"]
    assert radeon["spec"]["nodeSelector"]["amd.com/gpu.product-name"] == "AMD_Radeon_PRO_W7900"
    assert radeon["spec"]["containers"][0]["resources"] == {"limits": {"amd.com/gpu": "1"}}
    assert "tolerations" not in radeon["spec"] and len(radeon["spec"]["containers"]) == 1
    del api.pod_templates["radeon"]
    with pytest.raises(HTTPException) as gone:
        await _provider().launch("u" * 36, {}, {}, state.available_gpus[1])
    assert gone.value.status_code == 400


async def test_nvidia_defaults_yield_to_the_template(api):
    await kp.KubernetesProvider().inspect_self()
    gpu = {"device": "nvidia.com/gpu", "type": "nvidia", "resource": "nvidia.com/gpu", "node_selector": {}}
    template = {
        "spec": {
            "runtimeClassName": "gvisor",
            "containers": [{"name": "app", "env": [{"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "compute,video"}]}],
        }
    }
    pod = _provider()._pod_manifest("s" * 36, {}, {}, gpu, template)
    assert pod["spec"]["runtimeClassName"] == "gvisor"
    assert [e for e in pod["spec"]["containers"][0]["env"] if e["name"] == "NVIDIA_DRIVER_CAPABILITIES"] == [
        {"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "compute,video"}
    ]


async def test_unschedulable_pod_gives_up(api, monkeypatch):
    await kp.KubernetesProvider().inspect_self()
    api.pod_status = {
        "phase": "Pending",
        "conditions": [{"type": "PodScheduled", "status": "False", "message": "0/3 nodes: Insufficient amd.com/gpu"}],
    }
    monkeypatch.setattr(kp, "UNSCHEDULABLE_TIMEOUT", -1)
    with pytest.raises(HTTPException) as excinfo:
        await _provider().launch("s" * 36, {}, {}, None)
    assert excinfo.value.status_code == 503 and "Insufficient amd.com/gpu" in excinfo.value.detail


async def test_pinned_digest_becomes_the_session_image(api):
    await kp.KubernetesProvider().inspect_self()
    image = "lscr.io/linuxserver/firefox:latest"
    state.image_metadata[image] = {"pinned": "sha256:" + "a" * 64}
    template = {"spec": {"containers": [{"name": "app", "image": "x", "imagePullPolicy": "Always"}]}}
    pod = _provider()._pod_manifest("s" * 36, {}, {}, None, template)
    assert pod["spec"]["containers"][0]["image"] == "lscr.io/linuxserver/firefox@sha256:" + "a" * 64
    assert pod["spec"]["containers"][0]["imagePullPolicy"] == "IfNotPresent"
    info = await _provider().get_local_image_info(image)
    assert info["short_id"] == "a" * 12 and info["digests"] == ["lscr.io/linuxserver/firefox@sha256:" + "a" * 64]


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("firefox", ("registry-1.docker.io", "library/firefox", "latest")),
        ("linuxserver/firefox:1.2", ("registry-1.docker.io", "linuxserver/firefox", "1.2")),
        ("docker.io/library/nginx", ("registry-1.docker.io", "library/nginx", "latest")),
        ("lscr.io/linuxserver/firefox:latest", ("lscr.io", "linuxserver/firefox", "latest")),
        ("localhost:5000/app", ("localhost:5000", "app", "latest")),
        ("registry.test:5000/team/app:v2", ("registry.test:5000", "team/app", "v2")),
        ("ghcr.io/x/y@sha256:" + "b" * 64, ("ghcr.io", "x/y", "sha256:" + "b" * 64)),
    ],
)
def test_split_reference(image, expected):
    assert kp._split_reference(image) == expected


def test_docker_quantity():
    assert kp._docker_quantity("2g") == "2Gi"
    assert kp._docker_quantity("512m") == "512Mi"
    assert kp._docker_quantity("1024") == "1024"
    assert kp._docker_quantity("1.5G") == "1.5Gi"


async def test_session_pods_start_from_the_namespace_pod_template(api, monkeypatch):
    await kp.KubernetesProvider().inspect_self()
    api.pvc_modes["storage"] = ["ReadWriteOnce"]
    await kp.KubernetesProvider().inspect_self()
    api.pod_templates["sealskin-session"] = {
        "template": {
            "metadata": {"labels": {"team": "a"}, "annotations": {"note": "x"}},
            "spec": {
                "runtimeClassName": "gvisor",
                "priorityClassName": "desktops",
                "nodeSelector": {"pool": "desktops"},
                "tolerations": [{"key": "desktops", "operator": "Exists"}],
                "affinity": {"nodeAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": []}},
                "containers": [
                    {"name": "sidecar", "image": "proxy"},
                    {
                        "name": "app",
                        "image": "placeholder",
                        "imagePullPolicy": "Always",
                        "resources": {"requests": {"cpu": "1"}, "limits": {"memory": "8Gi"}},
                        "env": [{"name": "SITE", "value": "1"}],
                    },
                ],
            },
        }
    }
    monkeypatch.setattr(kp.KubernetesProvider, "_wait_until_ready", _ready)
    gpu = {"device": "amd.com/gpu", "type": "dri3", "resource": "amd.com/gpu", "node_selector": {}}
    await _provider(mem_limit="2g").launch("s" * 36, {"SUBFOLDER": "/s/"}, {"/storage/x": {"bind": "/config"}}, gpu)
    pod = api.posted[0]
    spec = pod["spec"]
    app, sidecar = spec["containers"]
    assert sidecar["name"] == "sidecar" and app["name"] == "app"
    assert app["image"] == "lscr.io/linuxserver/firefox:latest" and app["imagePullPolicy"] == "Always"
    assert app["resources"] == {"requests": {"cpu": "1"}, "limits": {"memory": "2Gi", "amd.com/gpu": "1"}}
    assert app["env"][0] == {"name": "SITE", "value": "1"} and {"name": "SUBFOLDER", "value": "/s/"} in app["env"]
    assert spec["runtimeClassName"] == "gvisor" and spec["priorityClassName"] == "desktops"
    assert spec["nodeSelector"] == {"pool": "desktops", "kubernetes.io/os": "linux"}
    assert spec["tolerations"] == [{"key": "desktops", "operator": "Exists"}, {"key": "amd.com/gpu", "operator": "Exists"}]
    assert "nodeAffinity" in spec["affinity"] and "podAffinity" in spec["affinity"]
    assert spec["automountServiceAccountToken"] is False
    assert pod["metadata"]["labels"]["team"] == "a" and pod["metadata"]["annotations"] == {"note": "x"}


async def test_pending_timeout_reports_the_last_warning(api, monkeypatch):
    await kp.KubernetesProvider().inspect_self()
    api.pod_status = {"phase": "Pending", "containerStatuses": [{"state": {"waiting": {"reason": "ContainerCreating"}}}]}
    api.events = [
        {"lastTimestamp": "2026-09-26T00:00:01Z", "message": "older"},
        {"lastTimestamp": "2026-09-26T00:00:09Z", "message": "MountVolume.MountDevice failed"},
    ]
    monkeypatch.setattr(kp, "PENDING_TIMEOUT", 0)
    with pytest.raises(HTTPException) as excinfo:
        await _provider().launch("s" * 36, {}, {}, None)
    assert excinfo.value.status_code == 504 and "MountVolume.MountDevice failed" in excinfo.value.detail
    assert api.deleted == ["sealskin-ssssssss-x1y2z"]


async def _ready(self, session_id, current_ip, env_vars, **kwargs):
    return await current_ip()
