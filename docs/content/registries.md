---
title: Registries and proxies
description: Pull images and catalogs from private registries and internal hosts, through a proxy or a mirror, with credentials kept on each node in the files their tools write.
---

# Registries and proxies

Everything a node fetches from outside comes from four kinds of place:
registries, for the session images, the VPN gateway image, and the packages
of PRoot Apps catalogs; the hosts that serve app store files and their
autostart scripts; the hosts that serve the logos a store names; and the
hosts that serve the metadata and icons of PRoot Apps catalogs. Sessions
fetch none of it: a session runs an image the node already pulled and
installs PRoot Apps from a folder the node already filled. So everything
here is set up on the node, by the administrator, in files the standard
tools write, and the web app shows none of it.

## Proxies and private certificate authorities

The server's own fetches honor the proxy variables of its environment,
`HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY`, and trust the certificate file
`SSL_CERT_FILE` or the directory `SSL_CERT_DIR` names, in addition to the
system's. Set them on the container. `NO_PROXY` must name the other nodes of
a cluster, the identity provider when it is internal, and the object store
when it is, since every HTTP call of the server follows the variables.

Image pulls are not the server's: on Docker the daemon pulls, and on
Kubernetes the kubelet does. The daemon takes its proxy from its service
environment (`HTTP_PROXY` in a drop-in of `docker.service`) and trusts a
private authority through `/etc/docker/certs.d/<registry>/ca.crt`; a kubelet
takes both from the node it runs on. Sessions have the network of their
container and nothing else.

## Credentials

Two files on the node hold the credentials, read at every use, so a rotated
credential needs no restart. Neither is in the shared store of a cluster,
which holds no secret, and neither is shown back by the API: every node is
given its own copy.

### Registries

`SEALSKIN_REGISTRY_AUTH_PATH` (`/config/.docker/config.json`) is a Docker
`config.json`, the file `docker login` writes, and the file a Kubernetes
pull secret of type `kubernetes.io/dockerconfigjson` holds. Its `auths` map
a registry host to a credential:

```json
{
  "auths": {
    "registry.example": { "auth": "dXNlcjpwYXNzd29yZA==" },
    "ghcr.io": { "username": "bot", "password": "ghp_..." },
    "https://index.docker.io/v1/": { "auth": "..." }
  }
}
```

`auth` is `user:password` in base64; `username` and `password` are taken as
they are. The Docker Hub entry is matched under any of the names Docker
writes for it. The file serves every pull on this node (session images, the
VPN gateway image, the web client export), the digest **Check** and **Pull**
in the Installed Apps panel, and the packages of PRoot Apps catalogs. The
`docker login` on the host is not read: the server's container pulls through
the socket with the credentials it sends itself, and the daemon holds none.
Credential helpers (`credsStore`, `credHelpers`) are not run; registries
whose tokens are short-lived (ECR, ACR) want a job on the host that rewrites
the file, or Kubernetes, where the kubelet signs in by the node's identity.

Write the file into the config volume on Docker. On Kubernetes, the pull
secrets the [session template](kubernetes.md#the-session-template) names in
`imagePullSecrets` serve too: the kubelet pulls with them, and the server
reads them through its Role to pin digests and fetch catalogs, so one secret
does for both. A file mounted at the path from a Secret adds to them and
takes precedence.

### Hosts

`SEALSKIN_NETRC_PATH` (`/config/.netrc`) is a netrc file, as `curl` and `git`
read it. Each `machine` is a host the node signs in to with basic
authentication: an app store on an internal server, the raw files of a
private repository on GitHub or GitHub Enterprise (a personal access token
as the password), the hosts of logos, and the metadata and icons of PRoot
Apps catalogs.

```
machine git.example login deploy password glpat-...
machine raw.githubusercontent.com login bot password ghp_...
```

The file serves those fetches only. The identity provider, a trusted proxy,
the object store, and the other nodes are signed in to as their own pages
describe.

## Mirrors

`SEALSKIN_IMAGE_MIRRORS` names a mirror per upstream registry, as
`upstream=mirror` pairs separated by commas:

```
SEALSKIN_IMAGE_MIRRORS=lscr.io=registry.example/lscr,ghcr.io=registry.example/ghcr,docker.io=registry.example/hub
```

Every pull then goes to the mirror: `lscr.io/linuxserver/firefox:latest` is
pulled as `registry.example/lscr/linuxserver/firefox:latest`, and `nginx` as
`registry.example/hub/library/nginx:latest`, the layout a proxying registry
(Harbor, Artifactory, a pull-through `registry`) exposes one upstream per
project. Store entries, installed apps, and catalog records keep the upstream
name, so the mirror can be set, changed, or dropped without touching them,
and the credential looked up is the mirror's. Tags and digests are kept as
they are, so a digest pinned on Kubernetes is pulled from the mirror by
digest. The rewrite covers the session images, the VPN gateway image, and
the packages of PRoot Apps catalogs.

Docker's own `registry-mirrors` setting covers Docker Hub alone; this
setting is what covers lscr.io and ghcr.io.

## PRoot Apps remotes

A [catalog](administration.md#proot-apps) names its apps by remote. A remote
is a GitHub `owner/repo`, whose metadata is read from raw.githubusercontent.com
and whose apps are tags of `ghcr.io/<owner>/<repo>`, or any repository built
the same way, named by the base URL `metadata/` is served under and the
registry its apps are tags of, one space apart:

```
https://git.example/raw/it/proot-apps/main registry.example/it/proot-apps
```

The base URL is anything that serves `metadata/metadata.yml` and
`metadata/img/<icon>` beneath it: a raw file endpoint of GitHub Enterprise or
GitLab, a web server, a bucket. The registry is any distribution registry;
the node's credential for it and its mirror apply. Inside a session the apps
of such a remote are installed by their full image name
(`registry.example/it/proot-apps:<app>`), as the apps of any remote but the
default one are.

## Without internet access

A deployment that reaches no public host runs with the pieces above:

1. Mirror the images into the internal registry and name the mirror in
   `SEALSKIN_IMAGE_MIRRORS`, or install each app by its internal image name.
2. Serve the store's `apps.yml` and the `autostart/` scripts from an internal
   host and add that store; put its logos on a host the nodes reach, or
   leave them, since a logo that cannot be fetched is shown as the default.
3. Serve a proot-apps repository's `metadata/` from an internal host and
   push its images to the internal registry; name both in the remote.
4. Give each node the two credential files, and set the proxy variables
   where an egress proxy is the way out.
