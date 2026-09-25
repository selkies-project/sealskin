"""Kubernetes implementation of the provider interface.

The server runs as a pod and starts every session as a pod in its own
namespace, through its service account and the REST API, so a namespaced Role
is all it needs and no namespace name is written anywhere. Storage reaches a
session the way the server sees it: each server path is mounted from the
volume that backs it, with a `subPath`; a volume only one node can mount (a
`ReadWriteOnce` claim, a `hostPath`) pins sessions to the server's node.
A session pod starts from the PodTemplate `<owner>-session` when the namespace
has one, which is where node placement, runtime classes, and default resources
belong. Session pods are labelled with the server's owner (its Deployment or
StatefulSet) and owned by it, so uninstalling removes them too. Images are
pulled by the kubelet, so `pull_image` pins the registry's current digest and
sessions run that exact image.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import posixpath
import re
import socket
import ssl
import time
from typing import Any

import httpx
from fastapi import HTTPException

from ..docker_utils import scan_render_nodes
from ..settings import settings
from ..state import state
from .base_provider import (
    INSTANCE_LABEL,
    MANAGED_BY_LABEL,
    BaseProvider,
    epoch,
    host_port,
    instance_labels,
)

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
#: Seconds a pod may take to be scheduled and pull its image.
PENDING_TIMEOUT = 900
#: Seconds the scheduler may find no node for a pod, long enough for an autoscaler to add one.
UNSCHEDULABLE_TIMEOUT = 300
#: Seconds a deleted pod gets to terminate before `stop` returns anyway.
STOP_TIMEOUT = 30
#: Seconds a failing image pull is retried before the launch gives up.
PULL_FAILURE_GRACE = 90
FATAL_WAITING_REASONS = {
    "CreateContainerConfigError",
    "CreateContainerError",
    "ErrImageNeverPull",
    "InvalidImageName",
    "RunContainerError",
}
PULL_WAITING_REASONS = {"ErrImagePull", "ImagePullBackOff"}
#: Volume sources that belong to one pod and cannot back a session's storage.
POD_LOCAL_VOLUMES = {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "gitRepo", "image", "projected", "secret"}
#: Inline network volumes every node can mount at once.
NETWORK_VOLUMES = {"azureFile", "cephfs", "glusterfs", "nfs"}
#: Label of the PodTemplates that are the namespace's GPU options, valued with the GPU's
#: kernel driver (`nvidia`, `amdgpu`, `i915`, `xe`, and so on).
GPU_TEMPLATE_LABEL = "sealskin.app/gpu"
#: Node label naming the model of a node's GPUs, by driver: GPU Feature Discovery's for
#: NVIDIA, the AMD node labeller's, and Intel's NFD rules for its discrete GPUs.
PRODUCT_LABELS = {
    "nvidia": "nvidia.com/gpu.product",
    "amdgpu": "amd.com/gpu.product-name",
    "i915": "gpu.intel.com/product",
    "xe": "gpu.intel.com/product",
}
#: Node label the NVIDIA GPU Operator sets when a node's GPUs serve virtual machines.
NVIDIA_WORKLOAD_LABEL = "nvidia.com/gpu.workload.config"
#: One entry per NVIDIA GPU the node's driver manages, render node or not.
NVIDIA_GPUS_DIR = "/proc/driver/nvidia/gpus"
#: Device plugin resource of each render node driver, for the server's own node.
DRIVER_RESOURCES = {
    "nvidia": "nvidia.com/gpu",
    "amdgpu": "amd.com/gpu",
    "i915": "gpu.intel.com/i915",
    "xe": "gpu.intel.com/xe",
}
MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
_DOCKER_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([bkmgt]?)b?\s*$", re.IGNORECASE)


class KubeError(Exception):
    """A request the API server answered with an error status."""

    def __init__(self, status: int, message: str) -> None:
        """Record the HTTP status and the server's message."""
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


class KubeClient:
    """Minimal API client authenticated as the pod's service account.

    Attributes:
        namespace: The namespace every object is created in.
        mounts: The server container's mounts as `(mount path, volume, subPath)`,
            longest path first.
        labels: The server pod's labels, which node-bound sessions follow.
        owner: Owner reference given to session pods, or `None`.
        shared_claims: Claims every node can mount (`ReadWriteMany`).
    """

    def __init__(
        self,
        base_url: str,
        namespace: str,
        verify: ssl.SSLContext | bool = True,
        token_path: str = os.path.join(SERVICE_ACCOUNT_DIR, "token"),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a client for `base_url` scoped to `namespace`."""
        self.namespace = namespace
        self.token_path = token_path
        self.http = httpx.AsyncClient(base_url=base_url, verify=verify, timeout=30.0, transport=transport)
        self.mounts: list[tuple[str, dict[str, Any], str]] = []
        self.labels: dict[str, str] = {}
        self.owner: dict[str, Any] | None = None
        self.shared_claims: set[str] = set()

    @classmethod
    def in_cluster(cls) -> KubeClient:
        """Build the client from the service account the kubelet mounts."""
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        with open(os.path.join(SERVICE_ACCOUNT_DIR, "namespace"), encoding="utf-8") as handle:
            namespace = handle.read().strip()
        verify = ssl.create_default_context(cafile=os.path.join(SERVICE_ACCOUNT_DIR, "ca.crt"))
        # Cluster CAs often lack the key identifiers strict mode demands.
        verify.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return cls(f"https://{host_port(host, port)}", namespace, verify)

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Send one request and return the decoded body.

        Raises:
            KubeError: When the API server answers with an error status.
        """
        with open(self.token_path, encoding="utf-8") as handle:
            token = handle.read().strip()
        response = await self.http.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs
        )
        if response.status_code >= 400:
            try:
                message = response.json().get("message") or response.text
            except ValueError:
                message = response.text
            raise KubeError(response.status_code, message)
        return response.json()

    def path(self, resource: str, name: str = "", group: str = "") -> str:
        """Return the URL path of a namespaced resource (collection when `name` is empty)."""
        base = f"/apis/{group}" if group else "/api/v1"
        return f"{base}/namespaces/{self.namespace}/{resource}{'/' + name if name else ''}"


_client: KubeClient | None = None


def kube() -> KubeClient:
    """Return the shared API client, creating it on first use."""
    global _client
    if _client is None:
        _client = KubeClient.in_cluster()
    return _client


def _docker_quantity(value: Any) -> str:
    """Translate a Docker size (`2g`, `512m`, bytes) into a Kubernetes quantity."""
    match = _DOCKER_SIZE_RE.match(str(value))
    if not match:
        return str(value)
    number, unit = match.groups()
    suffix = {"": "", "b": "", "k": "Ki", "m": "Mi", "g": "Gi", "t": "Ti"}[unit.lower()]
    return f"{number}{suffix}"


def _capability(name: str) -> str:
    """Return a capability the way Kubernetes spells it (no `CAP_` prefix)."""
    name = str(name).strip().upper()
    return name[4:] if name.startswith("CAP_") else name


def _gpu_type(resource: str) -> tuple[str, str] | None:
    """Return `(type, driver)` of a device plugin resource that is a whole GPU for a container.

    NVIDIA's device plugin names its resources as DNS subdomains (`gpu`, `gpu.shared`,
    `mig-1g.10gb`); the plugin that hands GPUs to virtual machines names them in capitals
    after the PCI database.
    """
    vendor, _, name = resource.partition("/")
    if vendor == "nvidia.com":
        return ("nvidia", "nvidia") if re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", name) else None
    if resource == "amd.com/gpu":
        return "dri3", "amdgpu"
    if resource in ("gpu.intel.com/i915", "gpu.intel.com/xe"):
        return "dri3", name
    return None


def _under(path: str, mount_path: str) -> bool:
    """Tell whether `path` is `mount_path` or inside it."""
    mount_path = mount_path.rstrip("/")
    return path == mount_path or path.startswith(mount_path + "/")


def _container_state(status: dict[str, Any], kind: str) -> dict[str, Any]:
    """Return the `waiting` or `terminated` details of the first container in that state."""
    return next(
        (cs["state"][kind] for cs in status.get("containerStatuses", []) if kind in cs.get("state", {})), {}
    )


def _merge(base: Any, ours: Any) -> Any:
    """Lay SealSkin's pod fields over a template's: maps merge, lists join, the rest is ours."""
    if isinstance(base, dict) and isinstance(ours, dict):
        return {**base, **{key: _merge(base.get(key), value) for key, value in ours.items()}}
    if isinstance(base, list) and isinstance(ours, list):
        return base + ours
    return ours


def _app_container(spec: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Split a pod spec's containers into the session's (named `app`, else the first) and the rest."""
    containers = spec.get("containers") or []
    app = next((c for c in containers if c.get("name") == "app"), containers[0] if containers else {})
    return app, [c for c in containers if c is not app]


def _overlay(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    """Lay pod template `top` over `base` as `_merge` does, merging their session containers."""
    app, others = _app_container(base.get("spec") or {})
    top_app, top_others = _app_container(top.get("spec") or {})
    spec = _merge(base.get("spec") or {}, {**(top.get("spec") or {}), "containers": []})
    spec["containers"] = [_merge(app, top_app)] + others + top_others
    return {"metadata": _merge(base.get("metadata") or {}, top.get("metadata") or {}), "spec": spec}


def _quota_applies(quota: dict[str, Any], spec: dict[str, Any]) -> bool:
    """Tell whether a ResourceQuota's scopes take in a GPU session pod built on `spec`.

    Scopes are matched as the quota admission does: a GPU session sets limits, so it is
    never `BestEffort`, and its priority class and deadline come from the session template.
    """
    terms = [{"scopeName": scope, "operator": "Exists"} for scope in quota.get("spec", {}).get("scopes", [])]
    terms += (quota.get("spec", {}).get("scopeSelector") or {}).get("matchExpressions", [])
    priority = spec.get("priorityClassName") or ""
    facts = {
        "NotBestEffort": True,
        "Terminating": "activeDeadlineSeconds" in spec,
        "NotTerminating": "activeDeadlineSeconds" not in spec,
    }
    for term in terms:
        if term["scopeName"] == "PriorityClass":
            values = term.get("values") or []
            matched = {"Exists": priority != "", "In": priority in values, "NotIn": priority not in values}.get(
                term.get("operator"), False
            )
        else:
            matched = facts.get(term["scopeName"], False)
        if not matched:
            return False
    return True


def _repository(image: str) -> str:
    """Return an image reference without its tag or digest."""
    name = image.partition("@")[0]
    return name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name


def _session_image(image: str) -> str:
    """Return the reference sessions run: the pinned digest once there is one."""
    digest = state.image_metadata.get(image, {}).get("pinned")
    return f"{_repository(image)}@{digest}" if digest and "@" not in image else image


def _split_reference(image: str) -> tuple[str, str, str]:
    """Split an image reference into registry host, repository, and tag or digest."""
    name, _, digest = image.partition("@")
    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = "registry-1.docker.io", name
        if "/" not in repository:
            repository = f"library/{repository}"
    if registry == "docker.io":
        registry = "registry-1.docker.io"
    if digest:
        return registry, _repository(repository), digest
    if ":" in repository.rsplit("/", 1)[-1]:
        repository, _, tag = repository.rpartition(":")
        return registry, repository, tag
    return registry, repository, "latest"


async def registry_digest(image: str) -> str | None:
    """Ask the image's registry for the digest its reference points to now.

    Anonymous bearer tokens cover public images on Docker Hub, GHCR, Quay, and
    other distribution registries; a private image yields `None`.
    """
    registry, repository, reference = _split_reference(image)
    if reference.startswith("sha256:"):
        return reference
    url = f"https://{registry}/v2/{repository}/manifests/{reference}"
    headers = {"Accept": ", ".join(MANIFEST_TYPES)}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.head(url, headers=headers)
            if response.status_code == 401:
                challenge = dict(
                    re.findall(r'(\w+)="([^"]*)"', response.headers.get("WWW-Authenticate", ""))
                )
                if "realm" not in challenge:
                    return None
                token_response = await client.get(
                    challenge["realm"],
                    params={k: v for k, v in challenge.items() if k in ("service", "scope")},
                )
                token_response.raise_for_status()
                body = token_response.json()
                headers["Authorization"] = f"Bearer {body.get('token') or body.get('access_token')}"
                response = await client.head(url, headers=headers)
            if response.status_code != 200:
                logger.warning("Registry answered %s for image '%s'.", response.status_code, image)
                return None
            digest = response.headers.get("Docker-Content-Digest")
            if not digest:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                digest = response.headers.get("Docker-Content-Digest") or (
                    "sha256:" + hashlib.sha256(response.content).hexdigest()
                )
            return digest
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not resolve the digest of image '%s': %s", image, exc)
        return None


class KubernetesProvider(BaseProvider):
    """Launches applications as pods in the server's namespace."""

    orphan_grace = PENDING_TIMEOUT + BaseProvider.orphan_grace

    async def inspect_self(self) -> None:
        """Read the server's own pod: its volumes, labels, and owner.

        Session storage is resolved against these mounts, so a server path on
        a volume sessions cannot share is reported here once.
        """
        api = kube()
        state.discovered_api_port = settings.api_port
        state.discovered_session_port = settings.session_port
        hostname = socket.gethostname()
        try:
            pod = await api.request("GET", api.path("pods", hostname))
        except KubeError as exc:
            if exc.status != 404:
                raise
            address = socket.getaddrinfo(hostname, None)[0][4][0]
            matches = await api.request(
                "GET", api.path("pods"), params={"fieldSelector": f"status.podIP={address}"}
            )
            if len(matches.get("items", [])) != 1:
                raise RuntimeError(f"Could not identify the server's own pod ({hostname}).") from exc
            pod = matches["items"][0]

        spec = pod["spec"]
        api.labels = pod["metadata"].get("labels", {})
        volumes = {volume["name"]: volume for volume in spec.get("volumes", [])}
        containers = spec.get("containers", [])
        container = next(
            (c for c in containers if any(_under(settings.storage_path, m["mountPath"]) for m in c.get("volumeMounts", []))),
            containers[0] if containers else {},
        )
        api.mounts = sorted(
            (
                (mount["mountPath"].rstrip("/") or "/", volumes[mount["name"]], mount.get("subPath", ""))
                for mount in container.get("volumeMounts", [])
                if mount["name"] in volumes
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        api.shared_claims = set()
        for _, volume, _ in api.mounts:
            claim = (volume.get("persistentVolumeClaim") or {}).get("claimName")
            if not claim:
                continue
            try:
                pvc = await api.request("GET", api.path("persistentvolumeclaims", claim))
            except KubeError as exc:
                logger.info("Cannot read claim '%s' (%s); sessions stay on this node.", claim, exc.message)
                continue
            modes = pvc.get("status", {}).get("accessModes") or pvc["spec"].get("accessModes", [])
            if "ReadWriteMany" in modes:
                api.shared_claims.add(claim)

        owner = next(
            (ref for ref in pod["metadata"].get("ownerReferences", []) if ref.get("controller")), None
        )
        if owner and owner["kind"] == "ReplicaSet":
            try:
                replica_set = await api.request("GET", api.path("replicasets", owner["name"], "apps/v1"))
                owner = next(
                    (r for r in replica_set["metadata"].get("ownerReferences", []) if r.get("controller")),
                    owner,
                )
            except KubeError as exc:
                logger.info("Cannot read ReplicaSet '%s' (%s).", owner["name"], exc.message)
                owner = None
        if owner is None:
            owner = {"apiVersion": "v1", "kind": "Pod", "name": pod["metadata"]["name"], "uid": pod["metadata"]["uid"]}
        api.owner = {
            key: owner[key] for key in ("apiVersion", "kind", "name", "uid")
        } | {"blockOwnerDeletion": False}
        state.instance_name = owner["name"]
        logger.info(
            "Kubernetes: namespace %s, owner %s/%s, %d mount(s).",
            api.namespace,
            owner["kind"],
            owner["name"],
            len(api.mounts),
        )
        storage_volume = self._volume_for(settings.storage_path)
        if storage_volume is None:
            logger.error(
                "The storage path %s is not on a volume session pods can mount; mount a "
                "PersistentVolumeClaim or a network volume there.",
                settings.storage_path,
            )

    def _volume_for(self, server_path: str) -> tuple[dict[str, Any], str] | None:
        """Return the server volume holding `server_path` and the `subPath` inside it."""
        for mount_path, volume, sub_path in kube().mounts:
            if _under(server_path, mount_path):
                if POD_LOCAL_VOLUMES.intersection(volume):
                    return None
                relative = posixpath.relpath(server_path, mount_path)
                path = posixpath.normpath(posixpath.join(sub_path, relative))
                return volume, "" if path == "." else path
        return None

    def _node_bound(self, volume: dict[str, Any]) -> bool:
        """Tell whether only the server's node can mount this volume alongside the server."""
        claim = (volume.get("persistentVolumeClaim") or {}).get("claimName")
        if claim:
            return claim not in kube().shared_claims
        return not NETWORK_VOLUMES.intersection(volume)

    async def detect_gpus(self) -> None:
        """Offer the GPUs sessions can request.

        PodTemplates labelled `sealskin.app/gpu` are the options when the
        namespace has any, each carrying what its GPU needs (a device plugin
        resource or a DRA claim, node placement). Otherwise, as nodes are
        cluster-scoped: the GPU resources schedulable nodes advertise or DRA
        device classes map, when the server may list them; else those the
        namespace's quota grants; else the GPUs of the server's own node.
        Resources a quota covering session pods caps at zero are left out,
        GPUs handed to virtual machines are skipped, and nodes labelled with
        their GPUs' model are offered per model.
        """
        api = kube()
        try:
            options = (
                await api.request("GET", api.path("podtemplates"), params={"labelSelector": GPU_TEMPLATE_LABEL})
            )["items"]
        except KubeError:
            options = []
        if options:
            named = sorted((o["metadata"]["name"], o["metadata"]["labels"][GPU_TEMPLATE_LABEL] or "gpu") for o in options)
            state.available_gpus[:] = [
                {"device": name, "driver": driver, "type": "nvidia" if driver == "nvidia" else "dri3", "template": name}
                for name, driver in named
            ]
            logger.info("Offering %d GPU option(s) from PodTemplates: %s", len(named), [name for name, _ in named])
            return
        session = (await self._pod_template() or {}).get("spec") or {}
        try:
            quotas = (await api.request("GET", api.path("resourcequotas")))["items"]
        except KubeError:
            quotas = []
        hard: dict[str, set[str]] = {}
        for quota in filter(lambda quota: _quota_applies(quota, session), quotas):
            for key, value in (quota.get("status", {}).get("hard") or quota["spec"].get("hard", {})).items():
                hard.setdefault(re.sub(r"^(requests|limits)\.", "", key), set()).add(str(value))
        try:
            nodes: list[dict[str, Any]] | None = (await api.request("GET", "/api/v1/nodes"))["items"]
        except KubeError as exc:
            logger.info("Cannot list nodes (%s).", exc.message)
            nodes = None
        offered: list[tuple[str, str | None]] = []
        for node in nodes or []:
            labels = node["metadata"].get("labels", {})
            if node.get("spec", {}).get("unschedulable") or labels.get(NVIDIA_WORKLOAD_LABEL, "container") != "container":
                continue
            for resource, amount in node.get("status", {}).get("allocatable", {}).items():
                gpu = _gpu_type(resource)
                if gpu and str(amount).isdigit() and int(amount) > 0:
                    offered.append((resource, labels.get(PRODUCT_LABELS[gpu[1]])))
        if nodes is not None:
            try:
                classes = (await api.request("GET", "/apis/resource.k8s.io/v1/deviceclasses"))["items"]
            except KubeError:
                classes = []
            # A DRA driver's class that pods request as an extended resource.
            names = [c.get("spec", {}).get("extendedResourceName", "") for c in classes]
            offered += [(name, None) for name in names if _gpu_type(name)]
        if nodes is None:
            local = [DRIVER_RESOURCES[g["driver"]] for g in scan_render_nodes() if g["driver"] in DRIVER_RESOURCES]
            # Headless NVIDIA nodes often load no DRM driver, so they have no render node.
            if os.path.isdir(NVIDIA_GPUS_DIR) and os.listdir(NVIDIA_GPUS_DIR):
                local.append("nvidia.com/gpu")
            offered = [(r, None) for r, values in hard.items() if _gpu_type(r) and "0" not in values] or [
                (r, None) for r in local
            ]
        gpus: dict[str, dict[str, Any]] = {}
        for resource, product in offered:
            if "0" in hard.get(resource, ()):
                continue
            kind, driver = _gpu_type(resource)
            device = f"{resource}/{product}" if product else resource
            gpus[device] = {
                "device": device,
                "driver": driver,
                "type": kind,
                "resource": resource,
                "node_selector": {PRODUCT_LABELS[driver]: product} if product else {},
            }
        state.available_gpus[:] = [gpus[device] for device in sorted(gpus)]
        logger.info("Detected %d GPU type(s): %s", len(state.available_gpus), sorted(gpus))

    async def get_local_image_info(self, image_name: str) -> dict[str, Any] | None:
        """Return the digest sessions run for an image, once one was pinned."""
        digest = state.image_metadata.get(image_name, {}).get("pinned")
        if not digest:
            return None
        return {"id": digest, "short_id": digest.split(":")[-1][:12], "digests": [f"{_repository(image_name)}@{digest}"]}

    async def get_remote_image_digest(self, image_name: str) -> str | None:
        """Return the digest the image's registry serves now."""
        return await registry_digest(image_name)

    async def pull_image(self, image_name: str) -> Any:
        """Pin the registry's current digest; nodes pull it when a session needs it.

        Raises:
            RuntimeError: When the registry cannot be asked.
        """
        digest = await registry_digest(image_name)
        if not digest:
            raise RuntimeError(f"Could not resolve image '{image_name}' in its registry.")
        state.image_metadata.setdefault(image_name, {})["pinned"] = digest
        logger.info("Pinned '%s' to %s.", image_name, digest)
        return digest

    async def prune_images(self) -> None:
        """Leave unused images to the kubelet's image garbage collection."""

    def _pod_manifest(
        self,
        session_id: str,
        env_vars: dict[str, str],
        volumes: dict[str, Any] | None,
        gpu_config: dict[str, Any] | None,
        template: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the session pod over the namespace's session template.

        The app's and its template's Docker run options are mapped onto the
        pod; the PodTemplate's container named `app`, or its first, is the
        base of the session container. What NVIDIA sessions get by default,
        the `nvidia` RuntimeClass and every driver capability, yields to the
        templates' own choice.

        Raises:
            HTTPException: When a server path is not on a volume sessions can mount.
        """
        api = kube()
        config = self.app_config["provider_config"]
        overrides = dict(config.get("docker_overrides") or {})
        base = dict((template or {}).get("spec") or {})
        app, others = _app_container(base)
        base.pop("containers", None)
        env = dict(env_vars)
        container_security: dict[str, Any] = {}
        pod_security: dict[str, Any] = {}
        resources: dict[str, dict[str, str]] = {"limits": {}, "requests": {}}
        mounts: list[dict[str, Any]] = []
        pod_volumes: list[dict[str, Any]] = []
        annotations: dict[str, str] = {}
        spec: dict[str, Any] = {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 5,
            "nodeSelector": {"kubernetes.io/os": "linux"},
        }
        node_bound = False

        def add_volume(source: dict[str, Any], mount_path: str, sub_path: str = "", read_only: bool = False) -> None:
            name = next((v["name"] for v in pod_volumes if v == {"name": v["name"], **source}), None)
            if not name:
                name = f"sealskin-{len(pod_volumes)}"
                pod_volumes.append({"name": name, **source})
            mount = {"name": name, "mountPath": mount_path}
            if sub_path:
                mount["subPath"] = sub_path
            if read_only:
                mount["readOnly"] = True
            mounts.append(mount)

        for server_path, bind in (volumes or {}).items():
            found = self._volume_for(server_path)
            if found is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"'{server_path}' is not on a volume session pods can mount.",
                )
            volume, sub_path = found
            source = {key: value for key, value in volume.items() if key != "name"}
            node_bound = node_bound or self._node_bound(volume)
            add_volume(source, bind["bind"], sub_path, bind.get("mode") == "ro")

        add_volume(
            {"emptyDir": {"medium": "Memory", "sizeLimit": _docker_quantity(overrides.pop("shm_size", "1g"))}},
            "/dev/shm",
        )
        for key, value in overrides.items():
            if key == "privileged":
                container_security["privileged"] = bool(value)
            elif key in ("cap_add", "cap_drop"):
                container_security.setdefault("capabilities", {})["add" if key == "cap_add" else "drop"] = [
                    _capability(cap) for cap in value
                ]
            elif key == "security_opt":
                for option in value:
                    name, _, setting = re.sub(r"[=:]", "\0", str(option), count=1).partition("\0")
                    if name == "seccomp" and setting == "unconfined":
                        container_security["seccompProfile"] = {"type": "Unconfined"}
                    elif name == "apparmor" and setting == "unconfined":
                        container_security["appArmorProfile"] = {"type": "Unconfined"}
                        annotations["container.apparmor.security.beta.kubernetes.io/app"] = "unconfined"
                    elif name == "no-new-privileges":
                        container_security["allowPrivilegeEscalation"] = setting == "false"
                    else:
                        logger.info("[%s] Security option '%s' has no pod equivalent.", session_id, option)
            elif key == "devices":
                for device in value:
                    host, _, rest = str(device).partition(":")
                    add_volume({"hostPath": {"path": host}}, rest.partition(":")[0] or host)
            elif key == "volumes":
                for host, bind in (value.items() if isinstance(value, dict) else ()):
                    add_volume({"hostPath": {"path": host}}, bind["bind"], read_only=bind.get("mode") == "ro")
                for mount in value if isinstance(value, list) else ():
                    host, _, rest = str(mount).partition(":")
                    path, _, mode = rest.partition(":")
                    add_volume({"hostPath": {"path": host}}, path, read_only=mode == "ro")
            elif key == "tmpfs":
                for path, options in value.items():
                    size = re.search(r"size=([^,]+)", str(options))
                    source: dict[str, Any] = {"medium": "Memory"}
                    if size:
                        source["sizeLimit"] = _docker_quantity(size.group(1))
                    add_volume({"emptyDir": source}, path)
            elif key == "mem_limit":
                resources["limits"]["memory"] = _docker_quantity(value)
            elif key == "nano_cpus":
                resources["limits"]["cpu"] = f"{int(value) // 1_000_000}m"
            elif key == "cpu_shares":
                resources["requests"]["cpu"] = f"{int(value) * 1000 // 1024}m"
            elif key == "network_mode" and value == "host":
                spec["hostNetwork"] = True
                spec["dnsPolicy"] = "ClusterFirstWithHostNet"
            elif key == "ipc_mode" and value == "host":
                spec["hostIPC"] = True
            elif key == "pid_mode" and value == "host":
                spec["hostPID"] = True
            elif key == "group_add":
                groups = [int(group) for group in value if str(group).isdigit()]
                if groups:
                    pod_security["supplementalGroups"] = groups
            elif key == "dns":
                spec["dnsPolicy"] = "None"
                spec["dnsConfig"] = {"nameservers": list(value)}
            elif key == "extra_hosts":
                aliases: dict[str, list[str]] = {}
                for host, address in value.items():
                    aliases.setdefault(address, []).append(host)
                spec["hostAliases"] = [{"ip": ip, "hostnames": names} for ip, names in aliases.items()]
            elif key == "sysctls":
                pod_security["sysctls"] = [{"name": k, "value": str(v)} for k, v in value.items()]
            elif key == "runtime":
                spec["runtimeClassName"] = value
            elif key == "labels":
                pass
            else:
                logger.info("[%s] Run option '%s' has no pod equivalent.", session_id, key)

        if gpu_config:
            # A GPU option from a PodTemplate brings its own resource or claim.
            if "resource" in gpu_config:
                resource = gpu_config["resource"]
                resources["limits"][resource] = "1"
                # GPU node pools are commonly tainted `nvidia.com/gpu`, also for MIG and shared GPUs.
                keys = [resource] + (["nvidia.com/gpu"] if gpu_config["type"] == "nvidia" else [])
                spec["tolerations"] = [{"key": key, "operator": "Exists"} for key in dict.fromkeys(keys)]
                spec["nodeSelector"].update(gpu_config.get("node_selector") or {})
            if gpu_config["type"] == "nvidia":
                if not any(item.get("name") == "NVIDIA_DRIVER_CAPABILITIES" for item in app.get("env", [])):
                    env.setdefault("NVIDIA_DRIVER_CAPABILITIES", "all")
                if "runtimeClassName" not in base:
                    spec.setdefault("runtimeClassName", "nvidia")
        if node_bound and api.labels:
            spec["affinity"] = {
                "podAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": [
                        {"labelSelector": {"matchLabels": api.labels}, "topologyKey": "kubernetes.io/hostname"}
                    ]
                }
            }

        image = _session_image(config["image"])
        tag = "@" if "@" in image else image.rsplit("/", 1)[-1].partition(":")[2]
        container: dict[str, Any] = {
            "name": "app",
            "image": image,
            # Kubernetes' own default for this image; a stored PodTemplate comes back
            # defaulted for its placeholder image instead.
            "imagePullPolicy": "Always" if tag in ("", "latest") else "IfNotPresent",
            "env": [{"name": name, "value": str(value)} for name, value in env.items()],
            "ports": [{"name": "http", "containerPort": int(config["port"])}],
            "volumeMounts": mounts,
        }
        resources = {kind: values for kind, values in resources.items() if values}
        if resources:
            container["resources"] = resources
        if container_security:
            container["securityContext"] = container_security
        if pod_security:
            spec["securityContext"] = pod_security
        spec["volumes"] = pod_volumes
        spec["containers"] = [_merge(app, container)] + others
        metadata: dict[str, Any] = {
            "generateName": f"sealskin-{session_id[:8]}-",
            "labels": {
                **(overrides.get("labels") or {}),
                "app.kubernetes.io/name": "sealskin",
                "app.kubernetes.io/component": "session",
                **instance_labels(session_id),
            },
        }
        if annotations:
            metadata["annotations"] = annotations
        if api.owner:
            metadata["ownerReferences"] = [api.owner]
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": _merge((template or {}).get("metadata") or {}, metadata),
            "spec": _merge(base, spec),
        }

    async def _pod_template(self, name: str = "") -> dict[str, Any] | None:
        """Return the pod template of a PodTemplate, `<owner>-session` by default, or `None` without one."""
        api = kube()
        try:
            found = await api.request("GET", api.path("podtemplates", name or f"{state.instance_name}-session"))
        except KubeError as exc:
            if exc.status in (403, 404):
                return None
            raise
        return found.get("template") or {}

    async def _create(self, session_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
        """Create the pod, dropping the `nvidia` RuntimeClass where the cluster has none."""
        api = kube()
        try:
            return await api.request("POST", api.path("pods"), json=manifest)
        except KubeError as exc:
            if manifest["spec"].get("runtimeClassName") == "nvidia" and 'RuntimeClass "nvidia"' in exc.message:
                logger.info("[%s] No nvidia RuntimeClass; using the default runtime.", session_id)
                del manifest["spec"]["runtimeClassName"]
                return await api.request("POST", api.path("pods"), json=manifest)
            raise

    async def _wait_running(self, session_id: str, name: str) -> str:
        """Wait until the pod runs and has an address.

        Raises:
            HTTPException: When the pod fails, cannot pull its image, or stays pending too long.
        """
        api = kube()
        deadline = time.monotonic() + PENDING_TIMEOUT
        since: dict[str, float] = {}

        def lasting(condition: str, active: bool, limit: float) -> bool:
            if not active:
                since.pop(condition, None)
                return False
            return time.monotonic() - since.setdefault(condition, time.monotonic()) > limit

        last_reason = "The session pod did not start in time."
        while time.monotonic() < deadline:
            pod = await api.request("GET", api.path("pods", name))
            status = pod.get("status", {})
            if pod["metadata"].get("deletionTimestamp"):
                raise HTTPException(status_code=500, detail="The session pod was deleted while starting.")
            if status.get("phase") == "Running" and status.get("podIP"):
                return status["podIP"]
            if status.get("phase") in ("Failed", "Succeeded"):
                ended = _container_state(status, "terminated")
                detail = ended.get("message") or ended.get("reason") or status.get("reason") or status["phase"]
                raise HTTPException(status_code=500, detail=f"The session pod stopped: {detail}")
            scheduling = next((c for c in status.get("conditions", []) if c.get("type") == "PodScheduled"), {})
            unschedulable = scheduling.get("status") == "False"
            if unschedulable:
                last_reason = f"The session pod cannot be scheduled: {scheduling.get('message', '')}"
            if lasting("unschedulable", unschedulable, UNSCHEDULABLE_TIMEOUT):
                raise HTTPException(status_code=503, detail=last_reason)
            waiting = _container_state(status, "waiting")
            reason = waiting.get("reason", "")
            if reason in FATAL_WAITING_REASONS:
                raise HTTPException(
                    status_code=500, detail=f"The session pod cannot start: {waiting.get('message') or reason}"
                )
            if reason in PULL_WAITING_REASONS:
                last_reason = f"The session image cannot be pulled: {waiting.get('message') or reason}"
            if lasting("pull", reason in PULL_WAITING_REASONS, PULL_FAILURE_GRACE):
                raise HTTPException(status_code=500, detail=last_reason)
            await asyncio.sleep(2)
        try:
            events = await api.request(
                "GET", api.path("events"), params={"fieldSelector": f"involvedObject.name={name},type=Warning"}
            )
            warnings = sorted(events.get("items", []), key=lambda event: event.get("lastTimestamp") or "")
            if warnings:
                last_reason = f"The session pod did not start: {warnings[-1].get('message', '')}"
        except KubeError:
            pass
        raise HTTPException(status_code=504, detail=last_reason)

    async def launch(
        self,
        session_id: str,
        env_vars: dict[str, str],
        volumes: dict[str, Any] | None = None,
        gpu_config: dict[str, Any] | None = None,
        network: str | None = None,
        is_collaboration: bool = False,
        master_token: str | None = None,
        initial_tokens: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create the session pod and wait until its web endpoint answers.

        See `BaseProvider.launch` for the arguments; `network` has no meaning here.

        Raises:
            HTTPException: When the pod is refused, fails, or never becomes ready.
        """
        try:
            template = await self._pod_template() or {}
            if gpu_config and "template" in gpu_config:
                gpu_template = await self._pod_template(gpu_config["template"])
                if gpu_template is None:
                    raise HTTPException(status_code=400, detail=f"The GPU option '{gpu_config['device']}' is gone.")
                template = _overlay(template, gpu_template)
            manifest = self._pod_manifest(session_id, env_vars, volumes, gpu_config, template)
            pod = await self._create(session_id, manifest)
        except KubeError as exc:
            logger.error("[%s] Kubernetes refused the session pod: %s", session_id, exc.message)
            raise HTTPException(
                status_code=500, detail=f"Kubernetes refused the session pod: {exc.message}"
            ) from exc
        name = pod["metadata"]["name"]
        logger.info("[%s] Created pod %s from image %s.", session_id, name, manifest["spec"]["containers"][0]["image"])
        try:
            ip_address = await self._wait_running(session_id, name)

            async def current_ip() -> str:
                return ip_address

            await self._wait_until_ready(
                session_id,
                current_ip,
                env_vars,
                is_collaboration=is_collaboration,
                master_token=master_token,
                initial_tokens=initial_tokens,
            )
        except (HTTPException, KubeError, httpx.HTTPError) as exc:
            await self.stop(name)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=500, detail=f"Kubernetes error: {exc}") from exc
        return {"instance_id": name, "ip": ip_address, "port": self.app_config["provider_config"]["port"]}

    async def stop(self, instance_id: str) -> None:
        """Delete a session pod and wait until it is gone, as a Docker stop does.

        Args:
            instance_id: Pod name.
        """
        api = kube()
        try:
            await api.request("DELETE", api.path("pods", instance_id), params={"gracePeriodSeconds": 5})
            deadline = time.monotonic() + STOP_TIMEOUT
            while time.monotonic() < deadline:
                await api.request("GET", api.path("pods", instance_id))
                await asyncio.sleep(0.5)
            logger.warning("Pod %s is still terminating.", instance_id)
        except KubeError as exc:
            if exc.status == 404:
                logger.info("Deleted pod %s.", instance_id)
                return
            logger.error("Error deleting pod %s: %s", instance_id, exc.message)
        except httpx.HTTPError as exc:
            logger.error("Error deleting pod %s: %s", instance_id, exc)

    async def is_running(self, instance_id: str) -> bool:
        """Tell whether a pod exists, is not being deleted, and has not terminated."""
        api = kube()
        try:
            pod = await api.request("GET", api.path("pods", instance_id))
        except KubeError as exc:
            if exc.status == 404:
                return False
            raise
        if pod["metadata"].get("deletionTimestamp"):
            return False
        return pod.get("status", {}).get("phase") not in ("Failed", "Succeeded")

    async def managed_instances(self) -> dict[str, float]:
        """Return this server's session pods with their creation times."""
        api = kube()
        pods = await api.request(
            "GET",
            api.path("pods"),
            params={"labelSelector": f"{MANAGED_BY_LABEL}=sealskin,{INSTANCE_LABEL}={state.instance_name}"},
        )
        return {
            pod["metadata"]["name"]: epoch(pod["metadata"]["creationTimestamp"])
            for pod in pods.get("items", [])
        }
