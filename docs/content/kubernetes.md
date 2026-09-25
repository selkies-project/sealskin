---
title: Kubernetes
description: Run the server in any namespace you administer; every session runs as a pod beside it, with no cluster-scoped objects or fixed namespace.
---

On Kubernetes the server runs as one pod and starts each session as a pod in
the same namespace, through a Role bound to its own service account. Nothing
is cluster-scoped and no namespace name is written anywhere, so a namespace
administrator can install it on any cluster, managed or not.

## What you need

* A namespace in which you can create Roles, RoleBindings, and pods.
* A PersistentVolumeClaim for the storage (home directories and shared
  files), which every session mounts. The manifest asks for `ReadWriteOnce`,
  which any default storage class provides, and every session then runs on
  the server's node; with a `ReadWriteMany` class (NFS, CephFS, Azure Files,
  EFS, and the like) sessions run on any node.
* A way for clients to reach port `8443`: a `LoadBalancer` Service, or an
  ingress or gateway that forwards HTTPS and WebSockets to it (see
  [Exposing the server](#exposing-the-server)).
* A namespace whose admission lets containers start as root, as the
  server's image and LinuxServer's application images do: the Pod Security
  `baseline` or `privileged` level, or, on OpenShift, the `anyuid` security
  context constraint for the service accounts the server and its sessions
  run as.
* Optionally, GPUs through a device plugin or a Dynamic Resource Allocation
  driver: the NVIDIA GPU Operator, device plugin, or DRA driver over the
  NVIDIA Container Toolkit v1.20.1 or higher, or AMD's or Intel's.

## Install

Download [`kubernetes/sealskin.yml`](https://github.com/selkies-project/sealskin/blob/main/kubernetes/sealskin.yml),
set `HOST_URL` to the address clients will use and, to spread sessions over
nodes, a `ReadWriteMany` access mode and class for `sealskin-storage`, then
apply it to your namespace:

```bash
kubectl apply -n <namespace> -f sealskin.yml
kubectl -n <namespace> rollout status deployment/sealskin
kubectl -n <namespace> cp <server pod>:/config/admin.json admin.json
kubectl -n <namespace> exec deployment/sealskin -- rm /config/admin.json
```

The file creates a ServiceAccount, the Role and RoleBinding, the two claims,
the Deployment of the [linuxserver/sealskin](https://github.com/linuxserver/docker-sealskin)
image, its Service, the session [PodTemplate](#the-session-template), and a
NetworkPolicy that lets only the server reach session pods where the network
plugin enforces policies. Import `admin.json` into a client as described in
[Getting Started](start.md#connect).

The Role grants pods (get, list, create, delete), pod templates and events
(get, list), claims and resource quotas (get, list), and ReplicaSets (get).
Deleting the Deployment removes every session pod with it, since the
Deployment owns them; `kubectl delete -n <namespace> -f sealskin.yml` also
deletes the claims and the data on them.

## How sessions run

The server picks the Kubernetes backend by itself when it runs in a pod with
a service account token (`SEALSKIN_DEFAULT_PROVIDER` can force either
backend). At start-up it reads its own pod: the volume behind each of its
paths, the owner Deployment or StatefulSet, and whether each claim is
`ReadWriteMany`.

A session pod mounts its home directory and shared files from the storage
claim with a `subPath`, exactly where a Docker session would find them, runs
the app's image with the session's environment, and is labelled with the
session and the server's Deployment, which also owns it. It never runs again
once it exits, gets no service account token, and is reached only by the
server's session proxy on its pod address. When the storage can only be
mounted on one node, session pods carry an affinity to the server pod.

Every 30 seconds the server checks its sessions against the cluster: a
session whose pod ended or was deleted or evicted is stopped and cleaned up,
and a pod labelled with this server that no session references is deleted
once it is old enough not to belong to a launch still in progress.

A launch waits for the pod to be scheduled and its image to be pulled, which
on a node that has never run the app can take minutes. It fails early when
the image cannot be pulled for a minute and a half or the container cannot be
created, after 5 minutes when no node can take the pod (long enough for an
autoscaler to add one), and after 15 minutes in any other state, each time
with the reason the scheduler, the kubelet, or the pod's last warning gave.

## The session template

Placement and runtime policy belong to the cluster, not to SealSkin, so every
session pod starts from the PodTemplate `<deployment>-session`
(`sealskin-session` in the manifest) when the namespace has one. SealSkin lays
its own fields over it: maps are merged, lists are joined, and its values win.
The template's container named `app` (or its first container) is the base of
the session container, and any other container is kept as a sidecar.

```yaml
apiVersion: v1
kind: PodTemplate
metadata:
  name: sealskin-session
template:
  spec:
    runtimeClassName: gvisor
    priorityClassName: desktops
    nodeSelector:
      node-pool: desktops
    tolerations:
    - key: desktops
      operator: Exists
    containers:
    - name: app
      image: sealskin-session   # replaced by the application's image
      resources:
        requests:
          cpu: "1"
          memory: 2Gi
        limits:
          cpu: "8"
          memory: 8Gi
```

Resources belong here on clusters that impose small default limits or require
them: a pod without its own requests and limits gets whatever the namespace's
LimitRange or admission policy sets, which can be too little for a desktop.
The template is read at every launch, so `kubectl edit podtemplate
sealskin-session` applies to the next session.

## Run options on a pod

An app's `docker_overrides` and a template's `DOCKER_*` settings are Docker
run options; the Kubernetes backend maps each onto the session pod:

| Run option | Template setting | Pod |
| --- | --- | --- |
| `privileged` | `DOCKER_PRIVILEGED` | `securityContext.privileged` |
| `cap_add`, `cap_drop` | `DOCKER_CAP_ADD`, `DOCKER_CAP_DROP` | `securityContext.capabilities` |
| `security_opt` | `DOCKER_SECURITY_OPT` | `seccomp=unconfined` and `apparmor=unconfined` as `Unconfined` profiles, `no-new-privileges` as `allowPrivilegeEscalation: false` |
| `shm_size` | `DOCKER_SHM_SIZE` | the size of the memory volume at `/dev/shm` (1 GiB by default, as on Docker) |
| `mem_limit` | `DOCKER_MEM_LIMIT` | `limits.memory` |
| `nano_cpus` | `DOCKER_NANO_CPUS` | `limits.cpu` |
| `cpu_shares` | `DOCKER_CPU_SHARES` | `requests.cpu` (1024 shares are one CPU) |
| `devices`, `volumes` | `DOCKER_DEVICES`, `DOCKER_BIND_MOUNTS` | `hostPath` volumes on the node (see below for devices) |
| `tmpfs` | `DOCKER_TMPFS` | memory volumes |
| `network_mode`, `ipc_mode`, `pid_mode` of `host` | `DOCKER_NETWORK_MODE`, `DOCKER_IPC_MODE`, `DOCKER_PID_MODE` | `hostNetwork`, `hostIPC`, `hostPID` |
| `dns` | `DOCKER_DNS` | `dnsPolicy: None` with those nameservers |
| `extra_hosts` | `DOCKER_EXTRA_HOSTS` | `hostAliases` |
| `sysctls` | `DOCKER_SYSCTLS` | `securityContext.sysctls` |
| `group_add` | `DOCKER_GROUP_ADD` | `securityContext.supplementalGroups` (numeric groups) |
| `runtime` | | `runtimeClassName` |

A pod cannot be granted a device the way `--device` grants one: a device
node mounted from the host opens only in a privileged container or where the
container runtime allows it anyway (`/dev/net/tun`, for one). GPUs and other
devices reach sessions through device plugins or DRA drivers, as
[GPUs](#gpus) describes.

Ulimits have no pod equivalent and are skipped with a log line. The cluster's
admission rules still apply: a namespace under the Pod Security `baseline`
level refuses privileged pods, added capabilities beyond its list, `hostPath`
volumes, host namespaces, and `Unconfined` seccomp or AppArmor profiles, and
the launch then fails with the reason the cluster gave.

## GPUs

A pod asks Kubernetes for a GPU rather than naming a device, so the launcher
offers GPU options, each a request sessions can make. The session is not told
a render node as on Docker: the image uses the GPU it is handed, whose
`/dev/dri` nodes keep the host's numbering.

### GPU options you define

When the namespace has PodTemplates labelled `sealskin.app/gpu`, they are the
GPU options, one per template. The label's value is the GPU's kernel driver
(`nvidia`, `amdgpu`, `i915`, `xe`, and so on), which decides the apps that
may use it, and the template holds what a session needs for that GPU; it is
laid over the [session template](#the-session-template) the same way. This
serves any vendor's device plugin, Dynamic Resource Allocation, and
namespaces where the server can detect nothing:

```yaml
apiVersion: v1
kind: PodTemplate
metadata:
  name: gpu-l40s
  labels:
    sealskin.app/gpu: nvidia
template:
  spec:
    nodeSelector:
      nvidia.com/gpu.product: NVIDIA-L40S
    containers:
    - name: app
      image: sealskin-session
      resources:
        limits:
          nvidia.com/gpu: 1
---
apiVersion: v1
kind: PodTemplate
metadata:
  name: gpu-radeon
  labels:
    sealskin.app/gpu: amdgpu
template:
  spec:
    resourceClaims:
    - name: gpu
      resourceClaimTemplateName: radeon   # a ResourceClaimTemplate in the namespace
    containers:
    - name: app
      image: sealskin-session
      resources:
        claims:
        - name: gpu
```

The options are read when the server starts, so restart it after changing
them.

### Detected GPUs

Without such templates, the server finds the GPUs itself:

* When it may list nodes, every whole GPU a schedulable node advertises to
  containers: `nvidia.com/gpu` and its MIG and shared variants,
  `amd.com/gpu`, `gpu.intel.com/i915`, and `gpu.intel.com/xe`, along with the
  resource a DRA device class maps a GPU driver's devices to. Nodes labelled
  with their GPUs' model, by NVIDIA's GPU Feature Discovery, AMD's node
  labeller, or Intel's NFD rules, are offered per model, and a session on one
  is pinned to that model; GPUs handed to virtual machines are skipped.
* Without that permission, the GPU resources the namespace's ResourceQuota
  grants, and failing that the GPUs of the node the server runs on, as on
  Docker, mapped to their resource names; an NVIDIA GPU counts there even on
  a node that loads no DRM driver.
* A resource that a ResourceQuota covering session pods caps at zero is never
  offered; a quota scoped to other priority classes than the session
  template's does not cover them.

Listing nodes and device classes needs a cluster-scoped grant, which only a
cluster administrator can make and which SealSkin does not require:

```bash
kubectl create clusterrole sealskin-gpus --verb=get,list \
  --resource=nodes,deviceclasses.resource.k8s.io
kubectl create clusterrolebinding sealskin-gpus-<namespace> \
  --clusterrole=sealskin-gpus --serviceaccount=<namespace>:sealskin
```

A session on a detected GPU requests one unit of its resource and tolerates
taints keyed by it and, for NVIDIA, by `nvidia.com/gpu`, which GPU node pools
commonly carry. NVIDIA sessions also get `NVIDIA_DRIVER_CAPABILITIES=all` and
the `nvidia` RuntimeClass where the cluster defines one, unless the templates
set their own; where the cluster has none, the default runtime is used.

## Images

Nodes pull images themselves, so on Kubernetes **Pull** in the Installed Apps
panel pins the digest the registry serves at that moment, and every later
session runs exactly that image; each node pulls it the first time it runs
it and reuses it after. With auto-update on, the digests are re-pinned every update interval. An
image that was never pinned since the server started runs by its tag under
the cluster's pull policy, which for `:latest` is to pull on every start.
Registries are asked anonymously, so a private image is not pinned and always
runs by its tag, pulled with the namespace's image pull secrets.

## Exposing the server

The Service in the manifest is a `ClusterIP`. Clients need to reach `8443`,
which carries the API and every session's WebSocket:

* **A load balancer.** Set the Service's `type` to `LoadBalancer`. The server
  terminates TLS itself with the certificate in `/config/ssl`, as on Docker,
  and `HOST_URL` is the load balancer's address.
* **An ingress or a gateway.** Forward all paths of a host to the Service's
  `https` port with TLS to the backend (the server's certificate can stay
  self-signed), WebSocket upgrades, and read timeouts of an hour or more,
  since a session is one long-lived WebSocket and a launch can wait for an
  image pull. Set `HOST_URL` to the host and the port it listens on, such as
  `sealskin.example.com:443`, so the generated configuration files point
  there. How backend TLS and timeouts are set depends on the controller.

The API port `8000` is only needed by the Chrome self-signed certificate
fallback and can stay inside the cluster.
