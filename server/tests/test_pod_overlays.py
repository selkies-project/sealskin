"""What a pod overlay kept in SealSkin may hold, and what is refused by name."""

import pytest

from app import pod_overlays

PLACED = """
metadata:
  labels: {team: a}
  annotations: {note: x}
spec:
  runtimeClassName: gvisor
  priorityClassName: opportunistic
  nodeSelector: {pool: desktops}
  tolerations: [{key: desktops, operator: Exists}]
  affinity: {nodeAffinity: {}}
  topologySpreadConstraints: []
  securityContext: {fsGroup: 1000}
  dnsPolicy: ClusterFirst
  hostAliases: [{ip: 10.0.0.1, hostnames: [db]}]
  imagePullSecrets: [{name: regcred}]
  resourceClaims: [{name: gpu, resourceClaimTemplateName: l40}]
  containers:
  - name: app
    resources:
      requests: {cpu: "1", memory: 2Gi, ephemeral-storage: 4Gi}
      limits: {cpu: 1200m, memory: 2Gi, ephemeral-storage: 4800Mi}
    securityContext: {allowPrivilegeEscalation: false}
"""


def test_an_overlay_takes_placement_runtime_and_the_session_containers_resources():
    overlay = pod_overlays.parse_overlay(PLACED)
    assert overlay["metadata"] == {"labels": {"team": "a"}, "annotations": {"note": "x"}}
    assert overlay["spec"]["runtimeClassName"] == "gvisor"
    assert overlay["spec"]["containers"][0]["resources"]["limits"]["memory"] == "2Gi"
    assert pod_overlays.parse_overlay("") == {} and pod_overlays.parse_overlay("  \n") == {}
    assert pod_overlays.parse_overlay("spec: {}") == {} and pod_overlays.parse_overlay("~") == {}


@pytest.mark.parametrize(
    ("text", "named"),
    [
        # Whatever would let the session read an object of the namespace.
        ("spec: {volumes: [{name: s, secret: {secretName: tls}}]}", "spec.volumes"),
        ("spec: {serviceAccountName: sealskin}", "spec.serviceAccountName"),
        ("spec: {serviceAccount: sealskin}", "spec.serviceAccount"),
        ("spec: {automountServiceAccountToken: true}", "spec.automountServiceAccountToken"),
        ("spec: {initContainers: [{name: i, image: busybox}]}", "spec.initContainers"),
        ("spec: {containers: [{name: app, env: [{name: A, valueFrom: {secretKeyRef: {name: s, key: k}}}]}]}", "app.env"),
        ("spec: {containers: [{name: app, envFrom: [{secretRef: {name: s}}]}]}", "app.envFrom"),
        ("spec: {containers: [{name: app, volumeMounts: [{name: s, mountPath: /s}]}]}", "app.volumeMounts"),
        ("spec: {containers: [{name: app, image: other}]}", "app.image"),
        ("spec: {containers: [{name: app, command: [sh]}]}", "app.command"),
        # Nor the node's namespaces, a node of its own choosing, or what SealSkin sets.
        ("spec: {hostNetwork: true}", "spec.hostNetwork"),
        ("spec: {hostPID: true}", "spec.hostPID"),
        ("spec: {nodeName: node-a}", "spec.nodeName"),
        ("spec: {restartPolicy: Always}", "spec.restartPolicy"),
        ("kind: Pod", "overlay.kind"),
        ("metadata: {name: x}", "metadata.name"),
        ("metadata: {ownerReferences: []}", "metadata.ownerReferences"),
    ],
)
def test_an_overlay_refuses_a_field_by_name(text, named):
    with pytest.raises(ValueError, match="is not a field a SealSkin pod overlay takes") as refused:
        pod_overlays.parse_overlay(text)
    assert named in str(refused.value)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("spec: {containers: [{name: sidecar}]}", "`app` alone"),
        ("spec: {containers: [{name: app}, {name: app}]}", "`app` alone"),
        ("spec: {containers: {name: app}}", "`app` alone"),
        ("spec: {containers: [{resources: {}}]}", "`app` alone"),
        ("metadata: {labels: {team: 1}}", "map of text"),
        ("metadata: {annotations: [a]}", "map of text"),
        ("spec: [runtimeClassName]", "spec is a map"),
        ("metadata: x", "metadata is a map"),
        ("- spec: {}", "is a pod template"),
        ("spec: {runtimeClassName: [", "is not YAML"),
    ],
)
def test_an_overlay_of_the_wrong_shape_is_refused(text, message):
    with pytest.raises(ValueError, match=message):
        pod_overlays.parse_overlay(text)


def test_gpu_options_are_named_overlays_with_a_driver():
    options = pod_overlays.parse_gpu_options(
        """
- name: l40s
  driver: nvidia
  template:
    spec:
      nodeSelector: {nvidia.com/gpu.product: NVIDIA-L40S}
      containers: [{name: app, resources: {limits: {nvidia.com/gpu: 1}}}]
- name: radeon.w7900
  driver: amdgpu
"""
    )
    assert [(o["name"], o["driver"]) for o in options] == [("l40s", "nvidia"), ("radeon.w7900", "amdgpu")]
    assert options[0]["template"]["spec"]["nodeSelector"] == {"nvidia.com/gpu.product": "NVIDIA-L40S"}
    assert options[1]["template"] == {}
    assert pod_overlays.parse_gpu_options("") == [] and pod_overlays.parse_gpu_options("[]") == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("name: l40s", "are a list"),
        ("- l40s", "is a map"),
        ("- {name: l40s, driver: nvidia, count: 2}", "count is not a field"),
        ("- {driver: nvidia}", "needs a `name`"),
        # The launcher's own words, a device on another node, and anything a PodTemplate could not be called.
        ("- {name: auto, driver: nvidia}", "other than 'auto'"),
        ("- {name: l40s@node-a, driver: nvidia}", "needs a `name`"),
        ("- {name: nvidia.com/gpu, driver: nvidia}", "needs a `name`"),
        ("- {name: L40S, driver: nvidia}", "needs a `name`"),
        ("- {name: l40s, driver: nvidia}\n- {name: l40s, driver: nvidia}", "named twice"),
        ("- {name: l40s}", "kernel `driver`"),
        ("- {name: l40s, driver: NVIDIA Corp}", "kernel `driver`"),
        ("- {name: l40s, driver: nvidia, template: {spec: {volumes: []}}}", "'l40s'.template.spec.volumes"),
        ("- [", "not YAML"),
    ],
)
def test_gpu_options_of_the_wrong_shape_are_refused(text, message):
    with pytest.raises(ValueError, match=message):
        pod_overlays.parse_gpu_options(text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        # The server reads the pull secrets itself, so a name typed bare is refused here.
        ("spec: {imagePullSecrets: [regcred]}", "imagePullSecrets is a list of maps"),
        ("spec: {imagePullSecrets: [{name: regcred, key: x}]}", "`name: <secret>`"),
        ("spec: {imagePullSecrets: [{name: ''}]}", "`name: <secret>`"),
        ("spec: {imagePullSecrets: {name: regcred}}", "imagePullSecrets is a list of maps"),
        ("spec: {tolerations: [desktops]}", "tolerations is a list of maps"),
        ("spec: {nodeSelector: [pool]}", "nodeSelector is a map"),
        ("spec: {nodeSelector: {pool: 1}}", "map of text"),
        ("spec: {runtimeClassName: [gvisor]}", "runtimeClassName is text"),
        ("spec: {affinity: preferred}", "affinity is a map"),
        ("spec: {containers: [{name: app, resources: 4Gi}]}", "app.resources is a map"),
        ("spec: {containers: [{name: app, securityContext: [x]}]}", "app.securityContext is a map"),
    ],
)
def test_a_field_of_the_wrong_shape_is_refused(text, message):
    with pytest.raises(ValueError, match=message):
        pod_overlays.parse_overlay(text)
