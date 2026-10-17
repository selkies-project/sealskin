---
title: VPN profiles
description: Route sessions through a provider's WireGuard or OpenVPN servers without changing the application images.
---

# VPN profiles

A VPN profile is a provider's WireGuard or OpenVPN configuration as SealSkin
keeps it: one template, the credentials it asks for, and a list of endpoints,
one per server. A user who picks a profile at launch gets a session whose
traffic leaves through that provider, and the session's image knows nothing
about it: no VPN client is installed in the application image, no
capability is granted to the session container, and nothing in the home
directory changes.

All of it is optional. Nothing runs and nothing is pulled until an
administrator imports a profile and a user picks it, and a deployment that
never does needs none of what this page describes: no gateway image, no
settings, and on Kubernetes none of the Role rules the hubs take. A node
that cannot run a gateway refuses the launch with the reason and runs every
other session as before.

## How a session is routed

On a node running Docker, a session that chose a profile and an endpoint is
started on an internal Docker network of its own. An internal network has no
route out, so the session reaches nothing on its own. Beside it, the node
runs a **gateway** container from the image `SEALSKIN_VPN_IMAGE`
(`ghcr.io/selkies-project/sealskin-vpn`) that holds the one tunnel to the
endpoint, forwards the sessions' traffic into the tunnel and nowhere else, and
answers their DNS through it. The session's `/etc/resolv.conf` names the
gateway, and a one-shot helper from the same image, run in the session's
network namespace right after it starts, points the session's default route
at the gateway. The server's own container joins the network to reach the
session as it reaches any other.

The gateway's firewall allows forwarding only from the sessions into the
tunnel interface, and its own output only to the endpoint it was given. A
VPN session on Docker reaches nothing but the gateway: not the host, not its
LAN, not another container. A tunnel that drops leaves the sessions with no
path out rather than with the node's connection, and a gateway container
that exits or turns unhealthy is started again in place by the node for the
sessions still using it, never rebuilt under them.

One gateway per profile and endpoint on a node carries every session that
picked them, so a provider's device limit is spent once per endpoint in use,
however many sessions share it. The gateway starts with the first session
that needs it and stops shortly after the last one ends. Its rendered files
live under `SEALSKIN_NODE_STATE_PATH/vpn/<key>` while it runs, and its
container is `sealskin-vpn-<key>`, which is where to look with
`docker logs` when a launch reports that the VPN did not connect.

## On Kubernetes

Pods have no internal network to share, and a session pod may land on any
node, so on Kubernetes the gateway is a **hub**: a pod per profile and
endpoint, owned by the server like a session pod, holding the one tunnel to
the provider and a WireGuard interface of its own behind a ClusterIP
Service. A session pod gets a native sidecar from the same image that
connects to the hub over the pod network with a key pair of its own, makes
that the pod's default route before the application starts, keeps the
cluster's networks on the pod's own route, and lets nothing else out; the
pod resolves names at the hub. One hub carries every session of the
deployment that picked its endpoint, wherever the pods run, so the
provider's connection is spent once per endpoint in use, and the hub goes
away with the last session using it.

The hub's profile is a Secret the pod mounts, which the server also updates
with the spokes, and the server adds each session's spoke to the running hub
through `pods/exec`. The Role in `kubernetes/sealskin.yml` grants the
Secrets, Services, and exec this takes; a deployment that will not use
profiles can leave those rules out, and a launch with a profile is then
refused with the API server's answer. The hub needs `NET_ADMIN`,
`/dev/net/tun` from the node, and IP forwarding, which a pod inherits from
its node; a cluster that turns forwarding off in pods needs the
`net.ipv4.ip_forward` sysctl allowed. The session pod's sidecar needs
`NET_ADMIN` and nothing else; the application container is unchanged. `SEALSKIN_VPN_HUB_SUBNET`
gives the hubs and spokes their addresses, and `SEALSKIN_VPN_LOCAL_CIDRS`
names the networks that stay direct beside the tunnel: the private ranges by
default, which cover a cluster's pod and Service ranges. Unlike a session on
Docker, a VPN session on Kubernetes still reaches everything in those
ranges, the cluster's LAN included; narrow the setting to the pod and
Service ranges where that is not wanted. The hub pod is
`sealskin-vpn-<instance>-<key>`, named after the server's `app.kubernetes.io/instance`
so that two servers in one namespace keep hubs of their own, and its log is
where to look when a launch reports that the VPN did not connect. A hub pod
that fails or is evicted is created again over its Secret, with the keys it
had, so the sessions' spokes reconnect to it.

## Importing a profile

**VPN Profiles** in the dashboard imports a profile from one `.ovpn` or
`.conf` file, or from a zip of them as the providers hand them out. The
importer strips what names a server from every file and keeps the rest as the
template; files that share a template become one profile with an endpoint per
server, and a file whose template differs becomes a profile of its own, named
after the upload and what sets it apart.

For OpenVPN, the server lines (`remote`, `proto`, `port`, `remote-random`)
become the endpoints, the certificates stay inline in the template, and a
config that refers to separate `ca`, `cert`, `key`, or `tls-auth` files has
them inlined from the same zip. A config with `auth-user-pass` asks for the
provider's **service credentials**, which the import form takes; these are
not the account's password at most providers. Script directives (`up`,
`down`, `route-up`, `plugin`, `tls-verify`, and the like) are dropped with a
line in the log, since the gateway runs as root and runs no program a config
names. For WireGuard, the
`[Interface]` section is the template and each `[Peer]` an endpoint, with its
preshared key when it has one.

What the big providers hand out, as of this writing:

| Provider | OpenVPN | WireGuard |
| --- | --- | --- |
| NordVPN | Public zip of every server in UDP and TCP folders; service credentials | No manual config (the key is issued by their API) |
| Surfshark | Public zip; service credentials | One file per location from the account page |
| Private Internet Access | Public zips in several cipher and port variants; account credentials | No static config |
| IVPN | Public zip; the account id as username | Register a key, then one file per server |
| Proton VPN | Per-file downloads from the account page; OpenVPN credentials | One file per server, each with a key and an expiry of its own |
| Mullvad | Dropped in January 2026 | Zip of every relay sharing one interface |
| Windscribe, AirVPN, TorGuard | Config generators | Config generators |
| ExpressVPN, CyberGhost | Per-location downloads (CyberGhost ships separate certificate files) | None |

A profile keeps its template, the OpenVPN username and password, and the
WireGuard preshared keys as its secrets. On a cluster with
`SEALSKIN_STORE_KEY` set they are sealed under the cluster key before the
record leaves the node, the way the users' shared files are
([Object store](object-store.md)); without one they are kept as the users'
files are. The dashboard never shows them again; an edit can replace the
credentials.

## Publishing and editing endpoints

A freshly imported profile offers its users the first endpoint alone. In the
profile's editor, tick the endpoints users may pick, in the order to offer
them: the launcher then shows an **endpoint** field after the profile, with
the first ticked endpoint as the default. A provider's zip of thousands of
servers is best published as a handful of locations; the search box finds
them by label or host.

Every endpoint is editable in place: its label, host, port, and for OpenVPN
the protocol, for WireGuard the server's public key and preshared key, which
the editor never shows again once saved. An endpoint can be removed, added
by hand with **Add endpoint**, or added from another config file or archive
with **Add from file**, whose endpoints are appended to the profile under the
profile's template. A pasted config with several `remote` lines or several
`[Peer]` sections imports as several endpoints the same way. A gateway
already up for an endpoint keeps the settings it started with until it stops.

**Connection cap** limits how many gateways the whole deployment runs for the
profile at once, which is how many of the provider's connections it spends:
every node counts its own gateways and the sessions the other nodes last
reported, and a launch past the cap is refused with the count, so the user
can pick an endpoint already in use. **Users** and **Groups** say who may
pick the profile; Everyone unless names are chosen. The **Allow VPN
Profiles** switch in a user's or group's settings turns the choice off for an
account altogether. The profiles table shows every connection up across the
deployment with its node and the sessions on it.

## Settings

| Setting | Meaning |
| --- | --- |
| `SEALSKIN_VPN_IMAGE` | The gateway image, and the helper that sets a session's route. A node pulls it on first use; pin it to the server's version (`sealskin-vpn:<version>`) rather than `latest`, so that a pull on a new node brings the image the server was tested with. |
| `SEALSKIN_VPN_SUBNETS` | The range the gateways' internal networks are carved from, one `/24` each (`10.230.0.0/16`). Change it when it collides with a network the host reaches. |
| `SEALSKIN_VPN_HUB_SUBNET` | On Kubernetes, the network a hub and its spokes take their addresses from (`10.231.0.0/16`), the hub at `.1`. |
| `SEALSKIN_VPN_LOCAL_CIDRS` | On Kubernetes, the networks a session pod and a hub keep reaching directly beside the tunnel: the private ranges by default. |
| `SEALSKIN_VPN_PROFILES_PATH` | The shared record of the profiles. |

The gateway image is built from `vpn/` in the repository and published to
the GitHub Container Registry from `main`, tagged with the version and
`latest`; `sealskin-vpn:dev` built from the checkout serves a test
deployment, with `SEALSKIN_VPN_IMAGE` naming it.
