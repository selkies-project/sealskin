---
title: Clusters
description: Running SealSkin on several servers, with shared users and applications, pools of nodes, placement, home directories that move, and a lab to try it in.
---

One SealSkin server is a cluster of one. Add servers and they become
**nodes** of the same cluster: they share users, groups, applications, and
templates, a user signed in at one node starts sessions on any of them, and
nothing is a master. This page is how that works and how to set it up; the
last section builds a cluster on one machine to try it.

Clusters serve the [web sign-in](signin.md). A key-file client (the browser
extension or mobile app with a configuration file) reaches the one node its
file names, and its sessions run there.

## Nodes and roles

Every node runs the same image. What a node does is its roles, set with
`SEALSKIN_NODE_ROLES`:

| Role | The node |
| --- | --- |
| `frontend` | Serves the web app, signs users in, and proxies their sessions to the node that runs them. |
| `runtime` | Runs sessions, in Docker or Kubernetes. |

The default is both. A node with only `runtime` needs no public name and no
published session port: browsers never reach it, the frontends do.

A node is its server key (`/config/ssl/server_key.pem`). Its id is a hash of
the public key, and the other nodes know it by the record it publishes: that
key, its roles, the address of its **peer listener**, and the certificate the
listener serves.

## What nodes need from the network

* Every node reaches every other node's peer listener, `SEALSKIN_PEER_PORT`
  (`8444`), at the address the node advertises in `SEALSKIN_NODE_ADDRESS`
  (`host:port`; by default the host of `HOST_URL`).
* Browsers reach the frontends' session port only.

**Cluster** in the dashboard shows each node as answering or not, with the
reason the last attempt failed. "Signed for another node" means the address a
node advertises leads to a different node, as when a port forward points
elsewhere than expected: correct `SEALSKIN_NODE_ADDRESS` and restart it.

The peer listener carries the nodes' calls to each other and the session
traffic a frontend proxies. Each node issues itself a certificate for it, and
a node trusts exactly the certificates in the records of the approved nodes.
Every call is signed with the calling node's server key. Do not publish the
peer listener beyond the nodes.

## The shared store

What the nodes share is a small set of objects: users and administrators
(`keys/`), groups, installed applications, application stores, templates, and
the cluster's own records (`cluster/`: nodes, pools, sign-in settings, home
directory locations, usage, and the hash of the root token). The store that
holds them is the authority, and it holds no secret: public keys and hashes
only.

`SEALSKIN_STORE_URL` chooses where it is:

| Value | The objects are |
| --- | --- |
| `file` (default) | The files under `/config/.config/sealskin` the server has always kept, [editable by hand](configuration.md). |
| `s3://<bucket>/<prefix>?endpoint=<url>&region=<region>` | In a bucket of any S3-compatible service, with `SEALSKIN_STORE_ACCESS_KEY` and `SEALSKIN_STORE_SECRET_KEY`. The service has to honor conditional writes (`If-Match`, `If-None-Match`). |

A node that joins with a join code keeps no store of its own: it reads and
writes the files of the node it joined through, over the peer listener. That
node holds the records, so it is the one to back up, and while it is away the
others keep running on the copy each keeps and nothing can be changed.

With a bucket no node holds the records. Every node keeps a copy of what it
reads, serves that while the bucket is unreachable, and looks for changes
every `SEALSKIN_STORE_POLL_SECONDS`; a node that changes something tells the
others to look at once. All nodes can be stopped and started again in any
order: each reads the bucket and finds its own sessions still running.

Two administrators changing the same record on two nodes never overwrite each
other unseen: the second write is refused, and the dashboard asks to reload.

## Adding a node

### With a join code

For a cluster whose store is the first node's files.

1. In the dashboard of the node that holds the records, open **Cluster** and
   choose **Add a node**. It shows two lines, good for an hour and for one
   node.
2. Start the new node with them:

   ```yaml
   environment:
     - SEALSKIN_JOIN_URL=alpha.example.com:8444
     - SEALSKIN_JOIN_CODE=3f9c2a71b0de.Jq0…
     - SEALSKIN_NODE_ROLES=runtime
     - SEALSKIN_NODE_ADDRESS=bravo.example.com:8444
   ```

The code is never sent: each side proves it holds it over the record it
sends, which is also how the new node knows it reached the right cluster
before any certificate is trusted. A request without a valid code is dropped,
so nothing waits for approval that an administrator did not start. The node
is approved as it joins, into the pool the code was issued for, and remembers
the cluster in `/config/.config/sealskin/node/join.yml`; the two variables can
be removed afterwards.

### With a bucket

Start the node with the same `SEALSKIN_STORE_URL` and keys. It registers
itself and waits: **Cluster** shows it as pending until an administrator
approves it. The first node of an empty bucket approves itself.

Whoever holds the bucket's keys can write any record, including approvals, so
those keys are the cluster's administrator credential. Give them to nodes
only.

### Suspending and removing

**Suspend** takes a node out of placement and out of the others' trust; its
running sessions go on, unreachable through the frontends until it is
approved again. **Remove** deletes its record. A removed node that is still
running registers again, unapproved.

## Pools

A pool is a named group of nodes. Each node is in one, `default` unless
`SEALSKIN_NODE_POOL` or an administrator says otherwise. A pool has:

* **Access.** An open pool takes anyone. A restricted pool takes
  administrators and the users and groups it names, or whose settings allow
  it. A pool a user's groups deny is closed to the user either way.
* **Cost.** What an hour of session spends of a user's
  [allowance](signin.md#groups-switches-and-limits), and separately what an
  hour of GPU session spends, so heavy nodes can drain it faster.
* **Stop when spent.** End the sessions of a user whose allowance runs out,
  instead of only refusing new ones.
* **Domain.** The domain the pool's nodes are named under, for your own
  reference; a node's names are set on the node.

## Where a session starts

A launch goes to one node:

1. A persistent home directory decides: the session starts on the node that
   holds it, or not at all while that node is away.
2. Otherwise the candidates are the answering `runtime` nodes in pools open
   to the user, under their session cap (`SEALSKIN_NODE_MAX_SESSIONS`), and,
   for a GPU session, with a GPU free.
3. The one with the fewest sessions per CPU wins, the node the user is signed
   in at on a tie. The launcher can name a pool or a node instead.

The frontend forwards the launch to that node and proxies the session from
it. The session's credentials never leave the node that runs it.

### GPUs

A session shares the GPU it is given with any other session on it: the device
is exposed whole, and SealSkin does not isolate one session's use of it from
another's. What it controls is who gets one:

* The **GPU** switch says whether a user's sessions may use a GPU.
* The **GPU sharing** switch, turned off, gives each of the user's GPU
  sessions a GPU no other session is on, and keeps others off it.
* `SEALSKIN_NODE_GPU_SLOTS` caps the GPU sessions of a node.
* A restricted pool keeps GPU nodes for the groups that should have them.

## Home directories

A home directory lives on one node. The cluster records which
(`cluster/homes/<user>.yml`), sessions that mount it start there, and the file
manager reaches it through any frontend. A new home directory is created on
the node placement picks.

**Moving** a home directory sends it to another node: stop the sessions using
it, then choose **Move** beside it, in the dashboard for any user or in the
launcher for your own where the **Move home directories** switch allows. The
directory is streamed over the peer listener, and only when the other node
has all of it does the record change and the original go.

## Shared files

Each user's shared files, mounted at `Desktop/files` in every persistent
session, are the same on every node. With a second node in the cluster
(`SEALSKIN_FILES_SYNC=auto`), the store holds them under `files/<user>/`: a
node brings its copy up to date before a session starts, and sends what
changed when the session stops and every few minutes while it runs. The last
writer of a file wins; when two nodes changed the same file, the losing
version is kept beside it as `<name>.conflict-<node>`. Files over 512 MB stay
on their node.

## Limits across nodes

Limits are counted over the whole cluster from what the nodes last told each
other, which they do every `SEALSKIN_PEER_POLL_SECONDS` and again just before
a launch is checked. A node that is away counts with the sessions it last
reported, so losing sight of a node never frees a slot. Usage is written to
the store every `SEALSKIN_USAGE_FLUSH_SECONDS`, so an allowance can be
overrun by about that much.

## The audit log

Every node keeps a log of what was done on it, one JSON line per event, in
`/config/.config/sealskin/node/audit/<day>.log`: sign-ins and refused root
tokens, launches and stops, nodes that joined, and every administrative
change with the administrator who made it. **Audit Log** in the
dashboard reads the logs of all answering nodes together, newest first, with
a search, a day or a range, and an export as CSV or JSON; the
[API](api.md#cluster) behind it is `GET /api/admin/cluster/audit`.

## What is not built yet

* A runtime node behind NAT, reached through a relay, and sessions that
  bypass the frontend. Today every frontend has to reach every runtime node's
  peer listener.
* Web sign-in in the browser extension and mobile app, which use key files
  and one node.
* Public share links for files on a node other than the one the link names.
* Application icons and meta-app home templates, which stay on the node they
  were uploaded to.
* Moving a running session between nodes. Stop it, move the home directory,
  start it again.

## A lab on one machine

Two nodes on one Docker host are enough to try everything on this page. Build
or pull the image, then start the first node:

```bash
docker network create sealskin-lab
mkdir -p lab/alpha/config/ssl lab/alpha/storage lab/bravo/config lab/bravo/storage
cp fullchain.pem lab/alpha/config/ssl/proxy_cert.pem      # a certificate covering *.<your name>
cp privkey.pem   lab/alpha/config/ssl/proxy_key.pem
docker run -d --name alpha --hostname alpha --network sealskin-lab \
  -e PUID=$(id -u) -e PGID=$(id -g) -e HOST_URL=<your name>:8443 \
  -e SEALSKIN_NODE_NAME=alpha -e SEALSKIN_NODE_ADDRESS=alpha:8444 \
  -p 8443:8443 -v $PWD/lab/alpha/config:/config -v $PWD/lab/alpha/storage:/storage \
  -v /var/run/docker.sock:/var/run/docker.sock lscr.io/linuxserver/sealskin:latest
cat lab/alpha/config/root_token
```

`<your name>` is a name with a certificate browsers trust, as the Duck DNS
certificate the [installer](start.md#with-the-installer-recommended) makes;
on a machine with no public name, `mkcert localhost` after `mkcert -install`
gives one for `localhost`. (Session isolation would also take wildcard DNS
and a certificate covering the name's subdomains: `mkcert "*.localhost"
localhost`.) Open `https://<your name>:8443/`, sign in with the root
token, install an application under **App Stores**, and launch it.

Then, in the dashboard, **Cluster → Add a node** gives a join code; start
the second node with it:

```bash
docker run -d --name bravo --hostname bravo --network sealskin-lab \
  -e PUID=$(id -u) -e PGID=$(id -g) -e HOST_URL=bravo:8443 \
  -e SEALSKIN_NODE_NAME=bravo -e SEALSKIN_NODE_ADDRESS=bravo:8444 -e SEALSKIN_NODE_ROLES=runtime \
  -e SEALSKIN_JOIN_URL=alpha:8444 -e SEALSKIN_JOIN_CODE=<code> \
  -v $PWD/lab/bravo/config:/config -v $PWD/lab/bravo/storage:/storage \
  -v /var/run/docker.sock:/var/run/docker.sock lscr.io/linuxserver/sealskin:latest
```

`bravo` appears under **Cluster** within a few seconds, approved and
answering. The container names matter: each server finds its own container
by its host name.

### Things to try

* **Placement.** Launch with **Where** set to `bravo`. `docker ps` shows the
  session container labeled for `bravo`, and the session opens through
  `alpha`.
* **A user with limits.** Create a group with a session limit of 1 and a user
  in it (or set up an identity provider under **Sign In** and sign in). A
  second launch is refused whichever node the first is on.
* **Deny wins.** Put the user in a second group that allows the GPU while the
  first denies it: the launcher offers none.
* **A restricted pool.** Create a pool, restrict it to a group, move `bravo`
  into it, and launch as a user outside the group: `bravo` is never chosen
  and cannot be named.
* **Moving a home.** Create a home directory, launch into it, stop the
  session, and move the home to `bravo`. The next launch into it starts on
  `bravo`, and `lab/bravo/storage/<user>/` now holds it.
* **Shared files.** Put a file in `Desktop/files` in a session on one node,
  stop the session, and start a persistent session on another: the file is
  there.
* **A node going away.** `docker stop bravo`. Within half a minute **Cluster**
  shows it as not answering with the reason, launches go elsewhere, and a
  launch into a home on `bravo` is refused with the reason. Its sessions are
  back when it is.
* **A restart.** Recreate `alpha` with sessions running on `bravo`: they keep
  running, and reopen through `alpha` once it is up.

### The same with a bucket

Create a bucket on any S3-compatible service and give both nodes
`SEALSKIN_STORE_URL`, `SEALSKIN_STORE_ACCESS_KEY`, and
`SEALSKIN_STORE_SECRET_KEY` instead of a join code. The second node shows
under **Cluster** as pending; approve it there. Stop every node, start them
in any order, and the cluster is as it was.
