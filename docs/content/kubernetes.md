---
title: Kubernetes
description: Run the server in any namespace you administer; every session runs as a pod beside it, with no cluster-scoped objects or fixed namespace.
---

On Kubernetes the server runs as one pod and starts each session as a pod in
the same namespace, through a Role bound to its own service account. Nothing
is cluster-scoped and no namespace name is written anywhere, so a namespace
administrator can install it on any cluster, managed or not.

## What you need

* A namespace you administer: Kubernetes' own `admin` role there grants all
  that `sealskin.yml` asks for, and what shapes session pods is set in
  SealSkin itself ([The session template](#the-session-template)).
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
  run as. Sessions through a [VPN profile](vpn.md) need the `privileged`
  level: the hub and the spoke add `NET_ADMIN`, and the hub mounts
  `/dev/net/tun` from the node.
* Optionally, GPUs through a device plugin or a Dynamic Resource Allocation
  driver: the NVIDIA GPU Operator, device plugin, or DRA driver over the
  NVIDIA Container Toolkit v1.20.1 or higher, or AMD's or Intel's.

## Install

Download [`kubernetes/sealskin.yml`](https://github.com/selkies-project/sealskin/blob/main/kubernetes/sealskin.yml),
set `HOST_URL` to the address clients will use and, to spread sessions over
nodes, a `ReadWriteMany` access mode and class for `sealskin-storage`, then
apply it to your namespace. The manifest names the node `sealskin` through
`SEALSKIN_NODE_NAME`, since a pod's host name changes with every rollout
and the name is the node's identity in its records; change it when you run
several servers against one store.

```bash
kubectl apply -n <namespace> -f sealskin.yml
kubectl -n <namespace> rollout status deployment/sealskin
kubectl -n <namespace> cp <server pod>:/config/admin.json admin.json
kubectl -n <namespace> exec deployment/sealskin -- rm /config/admin.json
```

The file creates a ServiceAccount, the Role and RoleBinding, the two claims,
the Deployment of the [linuxserver/sealskin](https://github.com/linuxserver/docker-sealskin)
image, its Service, and a NetworkPolicy that lets only the server reach
session pods where the network plugin enforces policies. Import `admin.json`
into a client as described in [Getting Started](start.md#connect).

Node placement, runtime class, resources, and GPU options of the session
pods are set in SealSkin by an administrator, as [pod
overlays](#the-session-template) kept in its own store, with nothing written
to the namespace. A cluster administrator may also keep PodTemplates beneath
them:
[`kubernetes/pod-templates.yml`](https://github.com/selkies-project/sealskin/blob/main/kubernetes/pod-templates.yml)
adds a session PodTemplate and a second Role, bound to the same service
account, that lets the server read PodTemplates. Kubernetes' namespace roles
(`admin`, `edit`, `view`) grant nothing on PodTemplates, and a Role grants
only what its author holds, so it is applied by someone who holds that
access:

```bash
kubectl apply -n <namespace> -f pod-templates.yml
```

For the web app, the server writes the root key, the same administrator's
application key, at its first start; read it, sign in at `https://<host>/`,
and delete it:

```bash
kubectl -n <namespace> exec deployment/sealskin -- cat /config/root_key
kubectl -n <namespace> exec deployment/sealskin -- rm /config/root_key
```

The web app needs a certificate browsers trust, and with session isolation
a name for every session, which on a cluster means an ingress or a gateway:
[Exposing the server](#exposing-the-server).

The Role grants pods (get, list, create, delete) and their logs (get),
events, claims, and resource quotas (get, list), and ReplicaSets (get); the
Role in `pod-templates.yml` grants pod templates (get, list).
Deleting the Deployment removes every session pod with it, since the
Deployment owns them; `kubectl delete -n <namespace> -f sealskin.yml` also
deletes the claims and the data on them.

## How sessions run

The server picks the Kubernetes backend by itself when it runs in a pod with
a service account token (`SEALSKIN_DEFAULT_PROVIDER` can force either
backend). At start-up it reads its own pod: the volume behind each of its
paths, the owner Deployment or StatefulSet, and whether each claim is
`ReadWriteMany`.

The server also takes each application's web client out of its image once
per image, with a short-lived pod `sealskin-web-<id>` of that image whose
log it reads and which it deletes at once; the Role grants `pods/log` for
that.

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

Placement, runtime policy, and resources are set for every session pod by
the **session overlay**, `kubernetes_session_overlay`: YAML of a pod
template, a `metadata` and a `spec`, which an administrator writes in the
dashboard, under **Kubernetes session overlay (YAML)** in the Cluster
section, or in the nodes' `SEALSKIN_KUBERNETES_SESSION_OVERLAY`. It is kept
in SealSkin's shared store and written to nothing in the namespace. SealSkin
lays its own fields over it: maps are merged, lists are joined, and its
values win; its container `app` is the base of the session container.

```yaml
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
The overlay is read at every launch, so a change applies to the next session,
and a value the nodes would refuse is not saved ([What a pod overlay
takes](#what-a-pod-overlay-takes)).

A cluster administrator may lay a PodTemplate beneath it,
`<deployment>-session` (`sealskin-session` in `pod-templates.yml`), which the
server reads when the namespace has one and the Role in that file lets it. A
PodTemplate takes any field; its container named `app` (or its first
container) is the base of the session container, and any other container is
kept as a sidecar.

```yaml
apiVersion: v1
kind: PodTemplate
metadata:
  name: sealskin-session
template:
  spec:
    runtimeClassName: gvisor
    containers:
    - name: app
      image: sealskin-session   # replaced by the application's image
```

## Pod templates per application

The session overlay applies to every application. What differs between
them, such as the labels and annotations an admission controller or policy
keys on, node selectors and tolerations, or resources, goes into the
**Kubernetes Pod Overlay** setting (`KUBERNETES_POD_OVERLAY`) in the
Container card of the [app template](administration.md#app-templates) those
applications use:

```yaml
metadata:
  labels:
    example.com/profile: cad
  annotations:
    example.com/policy: workstation
spec:
  nodeSelector:
    workload-type: cad
  tolerations:
  - key: workload-type
    operator: Equal
    value: cad
    effect: NoSchedule
  containers:
  - name: app
    resources:
      limits:
        memory: 16Gi
```

The template's **Kubernetes Pod Template** setting
(`KUBERNETES_POD_TEMPLATE`) names a PodTemplate of the namespace, for a
cluster administrator's templates; the template's overlay is laid over it.
A session pod is built from these layers, each laid over the last as
SealSkin lays its own fields:

1. the session PodTemplate, `<deployment>-session`;
2. the session overlay;
3. the PodTemplate the app template names;
4. the app template's overlay;
5. the [GPU option](#gpu-options-you-define) the user picked;
6. SealSkin's own fields: its labels, the volumes, and the session
   container's image, ports, and environment.

A GPU choice wins over the application's defaults, and SealSkin's fields win
over all. Every layer is read at every launch, and a launch whose overlay the
node refuses, or whose named PodTemplate the namespace lacks or the server may
not read, fails with that reason. Only an administrator sets the two template
settings, as with the `DOCKER_*` settings; the Docker backend ignores them,
and the pods that export a web client and the VPN gateways start from the
first two layers alone.

## What a pod overlay takes

An overlay kept in SealSkin, the session overlay, an app template's, or a
[GPU option](#gpu-options-you-define)'s template, takes:

| Part | Fields |
| --- | --- |
| `metadata` | `labels`, `annotations` |
| `spec` | `affinity`, `nodeSelector`, `tolerations`, `topologySpreadConstraints`, `runtimeClassName`, `priorityClassName`, `schedulerName`, `securityContext`, `dnsPolicy`, `dnsConfig`, `hostAliases`, `imagePullSecrets`, `resourceClaims`, `containers` |
| `containers` | The session container `app` alone, with `resources` and `securityContext` |

Any other field is refused by name, and a field of the wrong shape is
refused too, where the overlay is saved and again at launch: volumes,
environment, service accounts, init and sidecar containers, and the node's
namespaces among them. An overlay's spec names nothing of the namespace for a
session to read, so an administrator of SealSkin reaches no Secret,
ConfigMap, claim, or service account token through it; what needs those
belongs in a PodTemplate, which only someone the cluster's RBAC lets write it
can. Labels and annotations are passed as they are: where the cluster's
admission webhooks act on them, injecting a sidecar or a secret, they act on
an overlay's too. The environment comes from the app template, and its
`DOCKER_*` settings map the run options onto the pod ([Run options on a
pod](#run-options-on-a-pod)).

Two fields name objects of the namespace without letting the session read
them. `imagePullSecrets` hands the kubelet a Secret to pull the image with;
the server reads the session layers' pull secrets too, to pin images
([Images](#images)), and sends them only to the registries they name.
`resourceClaims` gives the session a device of a ResourceClaim or a
ResourceClaimTemplate. GPU resources and claims belong in GPU options, which
honor each user's GPU switch; in a session or app overlay they apply to every
session it shapes.

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
| `network_mode`, `ipc_mode`, `pid_mode` of `host` | `DOCKER_NETWORK_MODE`, `DOCKER_IPC_MODE`, `DOCKER_PID_MODE` | `hostNetwork`, `hostIPC`, `hostPID` (on Docker, `network_mode` is ignored when the server itself runs in a container, since the session joins the server's network instead) |
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

The GPU options are defined in SealSkin, in `kubernetes_gpu_options`, under
**Kubernetes GPU options (YAML)** in the Cluster section of the dashboard or
in the nodes' `SEALSKIN_KUBERNETES_GPU_OPTIONS`: a list, each entry a `name`
the launcher shows, the GPU's kernel `driver` (`nvidia`, `amdgpu`, `i915`,
`xe`, and so on), which decides the apps that may use it, and a `template`,
the [pod overlay](#what-a-pod-overlay-takes) that gives a session that GPU.
This serves any vendor's device plugin, Dynamic Resource Allocation, and
namespaces where the server can detect nothing:

```yaml
- name: gpu-l40s
  driver: nvidia
  template:
    spec:
      nodeSelector:
        nvidia.com/gpu.product: NVIDIA-L40S
      containers:
      - name: app
        resources:
          limits:
            nvidia.com/gpu: 1
- name: gpu-radeon
  driver: amdgpu
  template:
    spec:
      resourceClaims:
      - name: gpu
        resourceClaimTemplateName: radeon   # a ResourceClaimTemplate in the namespace
      containers:
      - name: app
        resources:
          claims:
          - name: gpu
```

A name is lowercase letters, digits, `-`, and `.`, and `auto` is the
launcher's own. PodTemplates labelled `sealskin.app/gpu`, valued with the
GPU's kernel driver, are GPU options too, offered beside the setting's when
the server may read them ([Install](#install)); an option of the setting
takes the place of a PodTemplate of its name. Both are read again whenever
the launcher lists the options and when a session starts, so adding,
changing, or removing one needs no restart.

### Detected GPUs

Without GPU options of either kind, the server finds the GPUs itself, once,
the first time it needs them: at startup, or when the last option is removed. Restart
it to offer GPUs the cluster gained since.

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
the `nvidia` RuntimeClass where the cluster defines one, unless the layers
of the pod set their own; where the cluster has none, the default runtime is
used.

## Images

Nodes pull images themselves, so on Kubernetes **Pull** in the Installed Apps
panel pins the digest the registry serves at that moment, and every later
session runs exactly that image; each node pulls it the first time it runs
it and reuses it after. With auto-update on, the digests are re-pinned every update interval. An
image that was never pinned since the server started runs by its tag under
the cluster's pull policy, which for `:latest` is to pull on every start.
The registry is asked with the pull secrets the [session overlay and
PodTemplate](#the-session-template) name, which the server reads through its
Role, and with the node's own credential file
([Registries and proxies](registries.md#credentials)); a private image
covered by neither is not pinned and runs by its tag. Images are pulled
through the mirrors `SEALSKIN_IMAGE_MIRRORS` names, by the kubelet and the
server alike.

## PRoot Apps catalogs

The server keeps the [catalogs](administration.md#proot-apps) under
`/storage/sealskin_proot_apps`, on the storage claim, and a session pod mounts
its catalog from there with a `subPath`, read-only, like its home directory.
The packages are fetched for the architecture of the node the server pod runs
on.

## Browser policies

The server writes the files of a [browser policy](administration.md#browser-policies)
under `/storage/sealskin_browser_policies`, on the storage claim, and a session
pod mounts each file from there with a `subPath`, read-only, at the paths the
browsers read.

## VPN profiles

A session that picks a [VPN profile](vpn.md) gets a native sidecar that
routes its pod through a hub pod the server runs per profile and endpoint,
behind a ClusterIP Service, so sessions on any node share one connection to
the provider. The Role grants the Secrets, Services, and `pods/exec` the
hubs take, which nothing else uses, so a deployment without profiles can
leave those rules out; the hub pod needs `NET_ADMIN`, `/dev/net/tun` from
the node, and the IP forwarding a pod inherits from it, and the sidecar
`NET_ADMIN` alone. [VPN profiles](vpn.md#on-kubernetes) has the details and
the settings.

## Exposing the server

The Service in the manifest is a `ClusterIP`. Clients reach the server one
of two ways.

### A load balancer

Set the Service's `type` to `LoadBalancer`. The server terminates TLS itself
on `8443`, which carries the API and every session's WebSocket, with the
certificate in `/config/ssl`, as on Docker, and `HOST_URL` is the load
balancer's address. The API port `8000` is only needed by the Chrome
self-signed certificate fallback and can stay inside the cluster.

### An ingress or a gateway

The controller holds the certificate and routes to the server's plain HTTP
port, which answers the controller alone
([the plain HTTP listener](reverse-proxy/index.md#the-plain-http-listener)).
Uncomment the four settings in `sealskin.yml`:

| Setting | Value |
| --- | --- |
| `SEALSKIN_PUBLIC_URL` | The web app's address, `https://sealskin.example.com`. Set `HOST_URL` to the same host, with the port when it is not 443. |
| `SEALSKIN_SESSION_DOMAIN` | With session isolation, the domain sessions open under: `example.com` gives `<session id>.example.com`. |
| `SEALSKIN_TRUSTED_PROXIES` | Where the controller's requests come from: the pod network (`10.244.0.0/16` on many clusters), or the node network for a controller on the host network. |
| `SEALSKIN_HTTP_PORT` | `8080`, the Service's `proxied` port. |

Then apply [`kubernetes/ingress.yml`](https://github.com/selkies-project/sealskin/blob/main/kubernetes/ingress.yml)
with your names in it:

```bash
kubectl -n <namespace> create secret tls sealskin-tls --cert=fullchain.pem --key=privkey.pem
kubectl -n <namespace> apply -f ingress.yml
```

The file is an Ingress with two hosts on the same backend, the web app's
name and the wildcard `*.example.com` that every session's name falls
under with [session isolation](reverse-proxy/index.md#session-isolation),
and, commented, the same as a Gateway and an HTTPRoute. Without session
isolation the wildcard host may go, and the certificate need cover the web
app's name alone; with it, the certificate in the secret has to cover both
hosts, so it is a wildcard one, which cert-manager issues over a DNS-01
challenge. A wildcard host takes the names one label under the domain that
no other Ingress or route names, so other applications on the domain keep
their own.

Nothing in it is specific to a controller: the backend is plain HTTP, and a
controller passes `Host`, WebSocket upgrades, and `X-Forwarded-Proto` as it
does for any application. Set `ingressClassName` or `gatewayClassName` to
your controller's. A session is one long-lived WebSocket; where the
controller ends connections after a fixed time whatever passes over them,
raise that limit.

Sessions are pods on the pod network, which `SEALSKIN_TRUSTED_PROXIES`
names whole here. The server knows its sessions' addresses and takes
nothing a request from one says about itself: not a forwarded client
address, and not a [sign-in header](reverse-proxy/sign-in.md).

Signing users in works as behind any reverse proxy: at SealSkin itself over
OpenID Connect or SAML, or by the controller's forward-auth, as
[Behind a reverse proxy](reverse-proxy/index.md) describes.

Run with Kubernetes 1.37 and Traefik 3.7 as the controller, through both
the Ingress and the Gateway API, with sessions on the server's node
(`ReadWriteOnce`) and on other nodes (`ReadWriteMany`).
