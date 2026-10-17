---
title: Object store
description: Keeping the records of a cluster in an S3 bucket, so every node reads the same users, groups, and applications and the whole deployment can be stopped and started again from nothing but the bucket and the nodes' storage.
---

A SealSkin cluster keeps what its nodes share, users and groups,
applications and their stores, templates, branding, and the cluster's own
records, as a small set of YAML objects. On one server they are the files
under `/config/.config/sealskin`. With `SEALSKIN_STORE_URL` set to a bucket
they live in the bucket instead, every node reads and writes them there, and
no node holds anything the others cannot do without. This page is how to set
that up and what to expect from it; the [cluster page](cluster.md) covers
nodes, pools, placement, and home directories.

## When to use a bucket

* Several nodes, and no wish to make one of them the keeper of the records
  that the others depend on.
* Nodes that come and go: a node started again on an empty configuration
  directory, with the same name and the same storage, is the same node.
* A deployment that can be stopped whole and started again in any order,
  from the bucket and each node's storage.

A single server needs none of this; `file`, the default, keeps it as it was.
A small cluster with a stable first node can also do without, with
[join codes](cluster.md#with-a-join-code).

## What is in the bucket

Every object is under the prefix the URL names. With `s3://records/prod`:

| Objects | What they are |
| --- | --- |
| `prod/keys/users/<name>`, `prod/keys/admins/<name>` | Users and administrators: settings and, for key-file clients, public keys. |
| `prod/groups/<name>` | Groups. |
| `prod/installed_apps.yml`, `prod/app_stores.yml`, `prod/app_templates/` | Installed applications, the stores they came from, and templates. |
| `prod/proot_catalogs.yml` | [PRoot Apps catalogs](administration.md#proot-apps). |
| `prod/branding/` | [Branding](administration.md#branding). |
| `prod/cluster/nodes/<name>.yml`, `prod/cluster/pools/` | The nodes and the pools. |
| `prod/cluster/homes/<user>.yml` | Which node holds each home directory. |
| `prod/cluster/usage/<day>/<node>.yml` | Session time per user, one object per node and day. |
| `prod/cluster/application_keys.yml`, `prod/cluster/settings.yml` | The hashes of the [application keys](signin.md#application-keys) and the sign-in settings. |
| `prod/cluster/leases/` | Short-lived locks, as the one a launch holds (below). |
| `prod/cluster/file_keys/<user>.yml` | Each user's file key, wrapped under the cluster key (below). |
| `prod/files/<user>/` | The users' [shared files](cluster.md#shared-files), once the cluster has a second node. |

The records hold no secret: public keys and hashes only, which is why the
bucket's keys can be shared by every node. Two things in the bucket are
secrets of their own: the users' shared files, which are their own content,
and the credentials an administrator enters in the dashboard, the OIDC
client secret in the sign-in settings and the credentials of the
[VPN profiles](vpn.md). With `SEALSKIN_STORE_KEY` set they are sealed under
the cluster key before they are written, as the section on
[encrypting the files](#encrypting-the-users-files) describes; without it
they are written as they are. Keep the bucket private, turn on server-side
encryption, and give its keys to nodes only: whoever holds them can write
any record.

What stays on a node, under its `/config` and `/storage`:

* Its server key and peer certificate, which identify it to the other nodes
  until it is started again with a new one.
* Its sessions, and the home directories of the users whose sessions ran
  there. Home directories are tied to a node; the cluster records which.
* Caches of the application stores and the autostart scripts, the audit log,
  and session time not yet written out.

## Setting it up

### On Amazon S3

1. Create a bucket in the region of your choice, with public access blocked
   and server-side encryption on; versioning is optional and gives a history
   of every record. The nodes never delete the bucket or change its settings.
2. Create an IAM user for the nodes with a policy limited to that bucket:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       { "Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::records" },
       { "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": "arn:aws:s3:::records/*" }
     ]
   }
   ```

3. Start every node with the bucket and the user's keys:

   ```yaml
   environment:
     - SEALSKIN_STORE_URL=s3://records/prod?region=us-east-1
     - SEALSKIN_STORE_ACCESS_KEY=AKIA…
     - SEALSKIN_STORE_SECRET_KEY=…
     - SEALSKIN_NODE_NAME=alpha
   ```

   `region` picks the endpoint, `https://s3.<region>.amazonaws.com`; the
   prefix after the bucket (`prod`) may be anything or nothing.

### Self-hosted

Any service with the S3 API does, as long as it honors conditional writes:
`If-None-Match: *` on a PUT must refuse an object that exists, and `If-Match`
must refuse one whose entity tag changed. Every node checks this before it
writes a record, with one throwaway object under `cluster/probe/`, and
refuses to start on a service that takes a write the condition should have
refused: the log says so and names the condition. Without that check two
nodes could overwrite each other's records unseen.

[SeaweedFS](https://github.com/seaweedfs/seaweedfs) is the one to reach for:
one container, one volume, and it passes the check. With the identity the
nodes use in `s3.json`:

```json
{"identities": [{"name": "sealskin",
  "credentials": [{"accessKey": "AKSEALSKIN", "secretKey": "a-long-random-secret"}],
  "actions": ["Read", "Write", "List"]}]}
```

```yaml
services:
  seaweedfs:
    image: chrislusf/seaweedfs:latest
    command: server -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json -dir=/data
    volumes:
      - ./seaweedfs:/data
      - ./s3.json:/etc/seaweedfs/s3.json:ro
    ports:
      - 8333:8333
```

Create the bucket once (`aws --endpoint-url http://<host>:8333 s3api
create-bucket --bucket records`) and start the nodes with it:

```yaml
- SEALSKIN_STORE_URL=s3://records/prod?endpoint=http://<host>:8333
- SEALSKIN_STORE_ACCESS_KEY=AKSEALSKIN
- SEALSKIN_STORE_SECRET_KEY=a-long-random-secret
```

The `region` in the URL may be left out for a service that does not care
about it. Put the service behind TLS when the nodes reach it over a network
you do not own, and keep its data volume in the backups the way you would
the bucket's.

Tried:

| Service | Result |
| --- | --- |
| Amazon S3 | A node and a second node ran against it, as this page describes. |
| SeaweedFS (`weed server -s3`, with `s3.config`) | Conditional writes honored; a node ran against it and kept its records there. |
| [RustFS](https://github.com/rustfs/rustfs) | Conditional writes honored. |
| [Garage](https://garagehq.deuxfleurs.fr/) 2.1 | Conditional writes ignored; refused by the check. |

MinIO was not tried: its community images are no longer published. A service
not on the list is tried the same way: start one node against it and read
its log.

## How the nodes use it

**Identity.** A node is its name: `SEALSKIN_NODE_NAME`, else the name of
its container in Docker, else its host name. Its record is
`cluster/nodes/<name>.yml` and carries its current server key and peer
certificate. A node started again with an empty configuration directory
publishes a new key under the same name and the others trust it at once, so
give every node a name of its own and keep it with the node's storage. Two
nodes under one name take the record from each other, which the log of each
reports. A node that comes up under a new name registers as a new node, and
the record under the old name stays until an administrator removes it, so
set `SEALSKIN_NODE_NAME` wherever the container's name or the host name can
change.

**Membership.** A node that can write the bucket is a member of the cluster
as soon as it registers; there is no approval, since the bucket's keys admit
to every record anyway. **Suspend** in the dashboard still takes a node out
of placement and out of the others' trust until it is approved again, across
restarts. **Remove** deletes the record of a node that is gone; one still
running registers again.

**Reading.** A node reads a record from the bucket when it needs it and keeps
a copy under `/config/.config/sealskin`, which it serves while the bucket is
unreachable; then nothing can be changed until it answers. Every
`SEALSKIN_STORE_POLL_SECONDS` a node lists the bucket for records the others
changed, and a node that changes one tells the others to look at once, so a
change made on one node shows on the others within a few seconds. Each poll
is a handful of list requests, about fifty thousand a day per node at the
default interval, which on Amazon S3 is a few cents.

**Writing.** Every write is conditional on the record being as the node last
read it. Two administrators changing the same record on two nodes never
overwrite each other unseen: the second write is refused, and the dashboard
asks to reload. Records written by machines, as home directory locations or
a node's own usage, are one per node or per user, so nodes do not contend
for them.

**Launches.** The count a session limit is checked against comes from what
the other nodes last reported. So the launch of a user with a session limit
or an allowance holds a lease, `cluster/leases/launch/<user>.yml`, from the
check until the session is registered, and a second launch of theirs on any
node waits for it and then counts the first. A lease a node took and could
not give back is taken over after two minutes.

**Stopping and starting.** Stop every node, in any order; start them again,
in any order. Each reads the bucket, registers itself, and finds its own
sessions still running on its host. A node started on an empty configuration
directory finds its home directories by its storage and records them again;
its session records went with the directory, so the sessions it ran are
ended.

## Encrypting the users' files

Server-side encryption on the bucket keeps the files from whoever gets at
the disks, not from whoever holds the bucket's keys. For that, give every
node a cluster key:

```yaml
- SEALSKIN_STORE_KEY=…      # any long random string, the same on every node
```

With it, a user's shared files are encrypted on the node before they are
sent and decrypted as they arrive, each user under a random key of their
own. That key is made the first time the user's files are sent and kept in
the bucket wrapped under the cluster key, so the bucket's keys alone read no
user's file, and the cluster key alone reads none either. The user holds
nothing and has nothing to lose. Deleting the user deletes their key, after
which no copy of their files in the bucket, nor in any backup of it, can be
read. The names of the files are not encrypted; their content is.

Turn the key on at any time: a file sent before is read as it is and
encrypted when it next changes. Changing the key is not supported: a node
with another key leaves the files it cannot read where they are, on both
sides, and says so in its log. Keep the key with the bucket's keys, in the
same secret store, and in the same backup; losing it loses every user's
shared files.

The files on a node are not encrypted: a session reads and writes them as
plain files, and a [public share](usage.md) is a plain copy on the node
that made it, under `/storage/sealskin_public`. Encryption is
of what leaves the node.

## Moving to another bucket

Stop every node. Copy the prefix (`aws s3 sync s3://old/prod
s3://new/prod`), point every node at the new bucket and keys, and start them.
Nothing in the records names the bucket.

## Backups

With versioning on, every version of every record is in the bucket. Without,
`aws s3 sync s3://records/prod ./backup` keeps a copy of all of them, which
restores by copying it back. What the bucket does not hold is on the nodes:
home directories under each node's `/storage`, and the files above 512 MB
that the shared files keep on their node.
