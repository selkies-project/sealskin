---
title: Getting Started
description: Install the server, connect a browser extension or mobile app, and launch your first isolated application.
---

SealSkin has two halves: a **server** that runs applications in Docker
containers and streams them, and a **client** (browser extension or mobile
app) that sends links, files and downloads to it. This page takes you from
nothing to a running session.

## What you need

* A Linux host with [Docker](https://docs.docker.com/engine/install/) and a
  user in the `docker` group. Sessions are ordinary containers on that host,
  so give it the CPU, memory and disk you would give the applications
  themselves.
* A hostname and a **trusted TLS certificate** if you want to use Firefox or
  the mobile apps. Chrome and other Chromium browsers also work with the
  self-signed certificate the container generates, at the cost of one extra
  step (see [Certificates](#certificates)).
* One TCP port reachable from wherever your clients are. The default is
  `8443`, which carries both the encrypted API and the streamed sessions.
  Port `8000` is a plain-HTTP fallback for the API that only the Chrome
  self-signed workflow needs.
* Optionally, a GPU. NVIDIA needs the proprietary driver (580 or newer) with
  the `nvidia-container-toolkit` and `nvidia-drm.modeset=1`; Intel and AMD
  need nothing beyond `/dev/dri`. See [Administration](administration.md#gpus).

SealSkin is designed to be exposed directly on the internet rather than placed
behind another reverse proxy: its own Caddy instance terminates TLS and
authenticates every request to a session.

## Install the server

### With the installer (recommended)

The [linuxserver/docker-sealskin](https://github.com/linuxserver/docker-sealskin)
repository ships an interactive script that creates the directories, obtains a
Let's Encrypt wildcard certificate through [Duck DNS](https://www.duckdns.org/),
writes a `docker-compose.yml` and starts the stack. Create a Duck DNS
subdomain and copy its token first, then:

```bash
mkdir sealskin && cd sealskin
bash <(curl -sSL https://raw.githubusercontent.com/linuxserver/docker-sealskin/refs/heads/master/install.sh)
```

The script asks for the user and group id to run as (defaults to yours), the
storage, config and certificate paths (defaults under the current directory),
the port to expose, your Duck DNS domain and token, and an e-mail address for
Let's Encrypt. It ends with the address of your server, for example
`https://sealskin.example.duckdns.org:8443`.

Forward the chosen port on your router to the host. If you access the server
from inside the same network, your router or a local DNS server has to resolve
the Duck DNS name to the host's private address ("split DNS"), because Duck
DNS points at your public address.

Running the script again in the same directory offers to renew the certificate
and restarts the stack with the new one.

### With docker compose

If you already have a certificate, or want to start with the self-signed one,
this is the equivalent compose file:

```yaml
---
services:
  sealskin:
    image: lscr.io/linuxserver/sealskin:latest
    container_name: sealskin
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=Etc/UTC
      - HOST_URL=sealskin.example.com   # optional
    volumes:
      - /path/to/sealskin/config:/config
      - /path/to/sealskin/storage:/storage
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - 8443:8443
      - 8000:8000   # optional, HTTP fallback for Chrome with a self-signed certificate
    restart: unless-stopped
```

A few things about this file are not negotiable:

* **The container must be named `sealskin`.** The server inspects its own
  container to learn the host paths behind `/config` and `/storage`, the
  externally mapped ports and the Docker network, and it finds itself by that
  name (falling back to its hostname).
* **The Docker socket is required.** Every session is a container the server
  starts through it, so the container's user must be allowed to use the
  socket. If launches fail with a permission error from Docker, make the
  socket group-writable on the host (`sudo chmod g+rw /var/run/docker.sock`).
* **`PUID` and `PGID` own everything.** The server, its files and every
  session container run as that user, so the `/config` and `/storage`
  directories on the host must be writable by it.
* **`HOST_URL`** is written into the generated admin configuration file so it
  can be imported as-is. Without it you edit the file once by hand.

To use your own certificate, place the PEM files at
`config/ssl/proxy_cert.pem` and `config/ssl/proxy_key.pem` before the first
start (or replace the self-signed ones later and restart). Everything else the
server needs it generates itself; [Configuration](configuration.md) lists the
files.

### Other ways to run it

The server is also published as a Python wheel on every release. It needs
Python 3.11 or newer, Caddy on the `PATH`, access to a Docker daemon and the
same key and certificate files. That route is meant for developers and is
described in [Development](development.md#running-it-locally).

## First run

On the first start with an empty `/config` the container:

1. generates a self-signed certificate for the proxy (`ssl/proxy_cert.pem`,
   `ssl/proxy_key.pem`) unless you supplied one,
2. generates the server's RSA key (`ssl/server_key.pem`) that anchors the
   end-to-end encryption,
3. finds no administrator and creates one named `admin`, writing its
   **private key** and connection details to `/config/admin.json`.

Copy `admin.json` somewhere safe and delete it from the server once you have
imported it into a client. It is the only copy of that private key; the server
keeps just the public half. If `HOST_URL` was not set, open the file and
replace the literal string `HOST_URL` with your server's hostname or IP.

Watch the log with `docker logs -f sealskin`. The server also warns there when
the proxy certificate is within 14 days of expiring.

## Install a client

| Client | Where | Notes |
| --- | --- | --- |
| Chromium browsers | [Chrome Web Store](https://chromewebstore.google.com/detail/sealskin-isolation/lclgfmnljgacfdpmmmjmfpdelndbbfhk) | Full feature set: context menus, download interception, streamed downloads. Works with self-signed certificates. |
| Firefox | [Firefox Add-ons](https://addons.mozilla.org/en-US/firefox/addon/sealskin-isolation/) | Context menus and uploads. Requires a trusted certificate. |
| iOS | [App Store](https://apps.apple.com/us/app/sealskin/id6758210210) | Launcher, files and admin dashboard; sessions open in Safari. Requires a trusted certificate. |
| Android | [Google Play](https://play.google.com/store/apps/details?id=io.linuxserver.sealskin) | Same as iOS; sessions open in a Custom Tab. Requires a trusted certificate. |

The zips, APK and IPA on the
[releases page](https://github.com/selkies-project/sealskin/releases) are the
same builds for sideloading; [Development](development.md#loading-the-extension-unpacked)
covers loading an unpacked extension.

The extension and the app are thin shells. Everything you see after
connecting, from the launcher to the admin dashboard, is served by your
server, so the clients rarely need updating.

## Connect

Open the extension (toolbar icon or its options page) or the app. With no
server configured you land on the **connection page**.

1. Under **Quick Setup**, upload `admin.json` or paste its contents and click
   **Apply Configuration**. The manual form underneath takes the same values
   individually: server address, API port, session port, username, your
   private key and the server's public key. It can also generate a key pair
   for you, which is how a user asks an administrator for an account without
   ever sending a private key.
2. Click **Login & Test**. The client performs the encrypted handshake, signs
   a token with your key and calls the server; on success it saves the
   configuration and opens the dashboard.

### Certificates

With the self-signed certificate, **Chrome** first tries `https://` on the
session port, and when the browser refuses the certificate it falls back to
plain `http://` on the API port for the encrypted API traffic (which is why
port `8000` is in the compose file). The served UI still has to load over
HTTPS: if the dashboard shows **Server unreachable**, click **Open server** to
visit `https://<server>:8443/ui/` in a normal tab, accept the certificate,
then **Retry**. After that both the API and the sessions use HTTPS.

**Firefox** does not let extensions talk to servers with untrusted
certificates, and the **mobile** WebViews reject them outright, so those
clients need a certificate the device already trusts. The installer's Duck DNS
certificate satisfies all of them.

## Add users and applications

As the administrator, the dashboard has everything you need for a first
session:

1. **App Store** lists the applications published by the default catalogue
   ([linuxserver/sealskin-apps](https://github.com/linuxserver/sealskin-apps)).
   Pick one, review who may use it (`all` by default) and **Save
   Installation**. The image is pulled on first launch, or ahead of time from
   **Installed Apps**.
2. **Users** creates accounts. Leave the public key blank to have the server
   generate a key pair; the resulting configuration file is shown once, for
   you to hand to the user, who imports it exactly as you imported
   `admin.json`. Each user's permissions (storage, sharing, GPU, hardening,
   limits) are set here or through **Groups**.

[Administration](administration.md) describes every panel.

## Launch something

Click the SealSkin toolbar icon. The launcher lists the applications you may
use; pick one, choose a GPU and storage if offered, and click **Launch**. The
server starts the container, waits until it answers, and opens the session in
a new tab through the session proxy.

From here on the interesting entry points are the right-click menu (open a
link, open a link as a file, send an image or video, search selected text),
**Send Next Download to SealSkin**, and the file manager. [Usage](usage.md)
covers them all.
