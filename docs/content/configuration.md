---
title: Configuration
description: What the server keeps in /config and /storage, the keys and certificates, and how to edit the YAML by hand.
---

The server has no database. Everything it knows is a file under two
directories, `/config` for configuration and `/storage` for user data, both
of which are volumes of the container. The paths on this page are the
defaults; every one of them can be moved with a
[setting](settings.md).

All configuration files are meant to be edited by hand as well as through
the admin panels. The server watches them and reloads a file when it changes
on disk, so an edit takes effect without a restart. Its own writes are atomic
(a temporary file replaced over the original) and it recognises them, so the
watcher only fires for edits made by someone else.

## Directory layout

```
/config
  admin.json                       first-run admin credentials, delete after import
  ssl/
    server_key.pem                 RSA key anchoring the end-to-end encryption
    proxy_cert.pem, proxy_key.pem  TLS certificate and key Caddy serves
  .config/sealskin/
    Caddyfile                      rendered from the template at every start
    app_stores.yml                 the catalogues to fetch
    app_stores_cache/              cached copy of each catalogue
    installed_apps.yml             installed applications (references + overrides)
    app_templates/*.yml            application templates
    autostart_cache/               cached autostart scripts per app
    keys/admins/<name>             administrator public keys
    keys/users/<name>              user settings and public keys
    groups/<name>                  group settings
    sessions.yml                   live sessions, rewritten by the server
    public_shares.yml              public share metadata

/storage
  <username>/<home>/               home directories, mounted at /config in sessions
  <username>/_sealskin_shared_files/   mounted at /config/Desktop/files in every persistent session
  sealskin_ephemeral/              cleanroom homes, deleted when the session stops
  sealskin_uploads/<username>/     chunked uploads in progress
  sealskin_public/                 files behind public share links
  sealskin_home_templates/         meta-app home templates
  sealskin_app_icons/              icons uploaded for meta-apps
```

Inside the container these paths are what the server sees. When it starts a
session it has to hand Docker **host** paths, so at start-up it inspects its
own container (found by the name `sealskin`, or by hostname) and records how
each mount maps to the host. The same inspection discovers the externally
mapped API and session ports, which are what generated configuration files
tell clients to connect to, and the Docker network sessions are attached to.

## Keys and certificates

| File | Purpose | Created by |
| --- | --- | --- |
| `ssl/server_key.pem` | The server's RSA private key. Clients hold the public half and use it to verify the server in the handshake and to wrap the session key. | The container on first start (4096-bit), or you. The server exits if it is missing. |
| `ssl/proxy_cert.pem`, `ssl/proxy_key.pem` | Served by Caddy on the session port. | The container on first start, as a self-signed pair; replace with a trusted certificate for Firefox and mobile. |
| `keys/admins/<name>` | An administrator's public key, PEM only. | The server (first run and the Admins panel) or you. |
| `keys/users/<name>` | A user's settings and public key. | The server (Users panel) or you. |

To provide your own server key before the first start:

```bash
openssl genpkey -algorithm RSA -out config/ssl/server_key.pem -pkeyopt rsa_keygen_bits:4096
openssl rsa -in config/ssl/server_key.pem -pubout    # the public key clients need
```

To provide your own administrator, place a public key PEM at
`keys/admins/<name>` before the first start; the server then skips creating
the default `admin` account and `admin.json`.

## admin.json

Written once, on the first start with no administrator present. It is a
client configuration file, the same shape the Users panel produces and the
connection page's **Export Config** writes:

```json
{
  "server_endpoint": "sealskin.example.com",
  "api_port": 8000,
  "session_port": 8443,
  "username": "admin",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...",
  "server_public_key": "-----BEGIN PUBLIC KEY-----\n..."
}
```

`server_endpoint` is the value of `HOST_URL`, or the literal string
`HOST_URL` when that variable was not set. The ports are the ones the
container was started with, as discovered from Docker. Delete the file after
importing it: nothing on the server reads it again, and it is the only copy
of the private key.

## Users, administrators and groups

An administrator is a file `keys/admins/<name>` containing a public key. A
user is a file `keys/users/<name>` with two sections:

```
--- Settings ---
active: true
group: none
persistent_storage: true
public_sharing: false
harden_container: false
harden_openbox: false
gpu: true
storage_limit: -1
session_limit: -1
--- Public Key ---
-----BEGIN PUBLIC KEY-----
...
-----END PUBLIC KEY-----
```

Missing settings take the defaults shown. A group is a file `groups/<name>`
holding a YAML mapping of the same keys; a user whose `group` names it gets
the group's values in place of their own for every key the group defines.
Names may contain letters, digits, `_` and `-`. Files starting with `.` are
ignored.

To rotate a user's key, replace the public key block; to disable a user
without deleting their storage, set `active: false`.

## Application stores

`app_stores.yml` is a list of catalogues:

```yaml
- name: SealSkin Apps
  url: https://raw.githubusercontent.com/linuxserver/sealskin-apps/refs/heads/master/apps.yml
```

The first start seeds it with the default store from
`SEALSKIN_APP_RESOURCE_PATH`. Each store's YAML is downloaded into
`app_stores_cache/` and refreshed by the auto-update job, or from the App
Store panel.

### App store catalogues

A catalogue is a YAML document with an `apps` list. Each entry describes an
image and what it can do:

```yaml
apps:
  - id: firefox                       # unique within the store
    name: Firefox
    logo: https://example.com/firefox.png
    url: https://github.com/linuxserver/docker-firefox
    provider: docker
    provider_config:
      image: lscr.io/linuxserver/firefox:latest
      port: 3000                      # the Selkies port inside the container
      type: app
      nvidia_support: true
      dri3_support: true
      url_support: true               # may be launched with a URL
      open_support: true              # may be launched with a file
      extensions: [html, htm, pdf]    # file types it is offered for
      autostart: true                 # fetch the image repository's autostart script
      docker_overrides:               # optional extra containers.run() arguments
        shm_size: 2g
```

YAML anchors are welcome; the default store uses them to share extension
groups between apps. When `autostart` is true the server fetches the
`autostart` (and `autostart-wayland`) script from the image's source
repository and caches it under `autostart_cache/`, writing it into the
session's home directory at launch so the application starts by itself.

## Installed applications

`installed_apps.yml` stores one record per installed app. A record references
the store entry and keeps only what the administrator changed under
`overrides`; the effective app is the store entry deep-merged with those
overrides, so store updates apply on the next cache refresh:

```yaml
- id: 5a8f1c2e-...                  # generated
  source: SealSkin Apps             # store name from app_stores.yml
  source_app_id: firefox            # id inside that store
  app_template: Default
  users: [all]
  groups: []
  auto_update: true
  home_directories: true
  is_meta_app: false
  overrides:                        # only what differs from the store entry
    name: Work Firefox
    provider_config:
      image: lscr.io/linuxserver/firefox:stable
      env:
        - {name: FOO, value: bar}
```

Meta-apps (from the App Laboratory) add `base_app_id` and
`home_template_name`, and keep their own `name`, `logo` and autostart
scripts under `overrides`. Records from versions before 0.3.0 were full
snapshots; they are migrated on first load, and the file is rewritten once.

## Application templates

Each file in `app_templates/` is one template:

```yaml
name: Kiosk
settings:
  SELKIES_UI_SHOW_SIDEBAR: "false"
  HARDEN_DESKTOP: "true"
  DOCKER_MEM_LIMIT: 4g
```

Values are strings. Keys that are not `DOCKER_*` become environment
variables of the session container; `DOCKER_*` keys are translated into
Docker run options (memory and CPU limits, capabilities, devices, bind
mounts, network mode and so on). The file name is derived from the name
(`kiosk.yml`); renaming the `name` key renames the file. The set of
variables the editor offers is `template_schema.yml` in the server package,
served to clients as data, and a template may contain keys the editor does
not know.

A directory of read-only default templates can be supplied with
`SEALSKIN_DEFAULT_APP_TEMPLATES_PATH`; when no template exists at all a blank
`Default` is written to `app_templates/default.yml`.

## Sessions and shares

`sessions.yml` is the server's record of live sessions: container ids,
addresses, tokens, storage paths and, for rooms, the collaboration tokens.
It exists so sessions survive a restart. The server rewrites it, skips
records it cannot parse and drops records whose containers no longer exist;
there is no reason to edit it.

`public_shares.yml` maps share ids to their owner, original file name,
password hash and expiry. The files themselves live under
`/storage/sealskin_public`. Expired entries are removed by a background job
every ten minutes.

## The Caddyfile

`Caddyfile` is rendered from the template inside the server package at every
start, substituting the two ports and the certificate paths, and Caddy is
started with it as a child process. It is not meant to be edited; if you
need a different proxy configuration, run the server from source with a
modified template.

## Environment

Everything else is an environment variable. The
[Settings Reference](settings.md) lists all of them with defaults; the
container additionally understands `PUID`, `PGID`, `TZ` and `HOST_URL`, and
the LinuxServer.io conventions (`FILE__` secrets, `UMASK`, Docker mods)
described in the
[image's README](https://github.com/linuxserver/docker-sealskin#readme).
