"""Pod overlays an administrator keeps in SealSkin, for sessions on Kubernetes.

Session pods are shaped in SealSkin's own settings, with nothing written to
the namespace: `kubernetes_session_overlay` lies under every session pod,
`KUBERNETES_POD_OVERLAY` in an app template under its applications' sessions,
and each entry of `kubernetes_gpu_options` is a GPU option a session may pick.
Each is YAML of a pod template, a `metadata` and a `spec`, laid over the
session pod the way a PodTemplate is (see `providers.kubernetes_provider`).

An overlay takes the pod's labels and annotations, its placement, runtime,
priority, DNS and security fields, the image pull secrets and device claims,
and the `app` container's resources and security context. Its spec names
nothing of the namespace for the pod to read: no volume, environment, service
account, other container, or host namespace, so an administrator of SealSkin
reaches no Secret, ConfigMap, claim, or token through the spec. Labels and
annotations are passed as they are; what the cluster's admission webhooks do
on them is the cluster's. PodTemplates, which only those the cluster's RBAC
lets write them can, keep the rest. A field outside the list is refused by
name rather than dropped, and a field of the wrong shape is refused too, so
an overlay never does less than it says.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from . import persistence

#: Pod template fields an overlay may hold.
TEMPLATE_FIELDS = frozenset({"metadata", "spec"})
#: Metadata an overlay may give a session pod; both are maps of text.
METADATA_FIELDS = frozenset({"labels", "annotations"})
#: Pod spec fields an overlay may set, with the shape each takes: a `map`, a `list` of maps, or `text`.
#: The shape is checked here, since the server reads some of them itself before Kubernetes sees the pod.
SPEC_FIELDS = {
    "affinity": "map",
    "containers": "list",
    "dnsConfig": "map",
    "dnsPolicy": "text",
    "hostAliases": "list",
    "imagePullSecrets": "list",
    "nodeSelector": "map",
    "priorityClassName": "text",
    "resourceClaims": "list",
    "runtimeClassName": "text",
    "schedulerName": "text",
    "securityContext": "map",
    "tolerations": "list",
    "topologySpreadConstraints": "list",
}
#: Fields of the session container (`app`, the only one an overlay may name) an overlay may set, and their shapes.
CONTAINER_FIELDS = {"name": "text", "resources": "map", "securityContext": "map"}
#: Fields of one GPU option.
GPU_OPTION_FIELDS = frozenset({"name", "driver", "template"})
#: What a GPU option is called: a name a PodTemplate could have, so the two kinds share one list.
_OPTION_NAME = re.compile(r"[a-z0-9]([-a-z0-9.]{0,61}[a-z0-9])?")
_DRIVER = re.compile(r"[a-z0-9_-]{1,32}")
#: Names the launcher keeps for itself (`auto` picks a GPU; `<device>@<node>` names one on another node).
RESERVED_OPTION_NAMES = frozenset({"auto"})


def _text_map(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"{where} is a map of text to text")
    return value


def _refuse_unknown(found: dict[str, Any], allowed: Any, where: str) -> None:
    unknown = sorted(set(found) - set(allowed))
    if unknown:
        raise ValueError(f"{where}.{unknown[0]} is not a field a SealSkin pod overlay takes")


def _check_shapes(found: dict[str, Any], shapes: dict[str, str], where: str) -> None:
    for key, shape in shapes.items():
        if key not in found:
            continue
        value = found[key]
        fits = (
            isinstance(value, dict)
            if shape == "map"
            else isinstance(value, str)
            if shape == "text"
            else isinstance(value, list) and all(isinstance(item, dict) for item in value)
        )
        if not fits:
            kind = {"map": "a map", "text": "text", "list": "a list of maps"}[shape]
            raise ValueError(f"{where}.{key} is {kind}")


def check_overlay(data: Any, where: str = "overlay") -> dict[str, Any]:
    """Return a parsed overlay as a pod template, `{}` for none, or refuse it.

    Args:
        data: The parsed YAML.
        where: Where the overlay comes from, for the error.

    Raises:
        ValueError: For anything but a pod template of the fields the module lists.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{where} is a pod template, a map with `metadata` and `spec`")
    _refuse_unknown(data, TEMPLATE_FIELDS, where)
    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"{where}.metadata is a map")
    _refuse_unknown(metadata, METADATA_FIELDS, f"{where}.metadata")
    for key in METADATA_FIELDS & set(metadata):
        _text_map(metadata[key], f"{where}.metadata.{key}")
    spec = data.get("spec") or {}
    if not isinstance(spec, dict):
        raise ValueError(f"{where}.spec is a map")
    _refuse_unknown(spec, SPEC_FIELDS, f"{where}.spec")
    containers = spec.get("containers") or []
    if not isinstance(containers, list) or len(containers) > 1:
        raise ValueError(f"{where}.spec.containers holds the session container `app` alone")
    for container in containers:
        if not isinstance(container, dict) or container.get("name") != "app":
            raise ValueError(f"{where}.spec.containers holds the session container `app` alone")
        _refuse_unknown(container, CONTAINER_FIELDS, f"{where}.spec.containers.app")
        _check_shapes(container, CONTAINER_FIELDS, f"{where}.spec.containers.app")
    _check_shapes(spec, SPEC_FIELDS, f"{where}.spec")
    if "nodeSelector" in spec:
        _text_map(spec["nodeSelector"], f"{where}.spec.nodeSelector")
    for entry in spec.get("imagePullSecrets") or []:
        if set(entry) != {"name"} or not isinstance(entry["name"], str) or not entry["name"]:
            raise ValueError(f"{where}.spec.imagePullSecrets names each Secret as `name: <secret>`")
    return {key: value for key, value in (("metadata", metadata), ("spec", spec)) if value}


def parse_overlay(text: str, where: str = "overlay") -> dict[str, Any]:
    """Parse an overlay typed into a setting, `{}` for an empty one.

    Raises:
        ValueError: For text that is not YAML or an overlay `check_overlay` refuses.
    """
    if not (text or "").strip():
        return {}
    try:
        data = persistence.parse_yaml(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{where} is not YAML: {exc}") from exc
    return check_overlay(data, where)


def parse_gpu_options(text: str) -> list[dict[str, Any]]:
    """Parse `kubernetes_gpu_options`: a list of a `name`, a kernel `driver`, and a `template` overlay each.

    Raises:
        ValueError: For text that is not such a list, a name used twice or kept by the launcher, or a
            template `check_overlay` refuses.
    """
    if not (text or "").strip():
        return []
    try:
        data = persistence.parse_yaml(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"the GPU options are not YAML: {exc}") from exc
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError("the GPU options are a list, each a `name`, a `driver`, and a `template`")
    options: list[dict[str, Any]] = []
    for index, entry in enumerate(data, 1):
        where = f"GPU option {index}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is a map with `name`, `driver`, and `template`")
        unknown = sorted(set(entry) - GPU_OPTION_FIELDS)
        if unknown:
            raise ValueError(f"{where}.{unknown[0]} is not a field a GPU option takes")
        name, driver = entry.get("name"), entry.get("driver")
        if not isinstance(name, str) or not _OPTION_NAME.fullmatch(name) or name in RESERVED_OPTION_NAMES:
            raise ValueError(f"{where} needs a `name` of lowercase letters, digits, '-' and '.', other than 'auto'")
        if any(option["name"] == name for option in options):
            raise ValueError(f"the GPU option '{name}' is named twice")
        if not isinstance(driver, str) or not _DRIVER.fullmatch(driver):
            raise ValueError(f"GPU option '{name}' needs the GPU's kernel `driver`, as `nvidia` or `amdgpu`")
        template = check_overlay(entry.get("template"), f"GPU option '{name}'.template")
        options.append({"name": name, "driver": driver, "template": template})
    return options
