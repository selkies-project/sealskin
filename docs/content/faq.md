---
title: Troubleshooting and FAQ
description: Certificates, Firefox and mobile requirements, the Docker socket, GPUs, storage and other things that go wrong.
---

## Connecting

<details>
<summary>The dashboard says "Server unreachable" right after I imported my configuration.</summary>

With a self-signed certificate the browser refuses the served UI until you
have accepted the certificate once. Click **Open server**, accept it in the
tab that opens, then **Retry**. If the address or ports are wrong, **Change
connection** takes you back to the connection page. Check that the session
port is reachable from where you are (`curl -k https://<server>:8443/ui/`).
</details>

<details>
<summary>Login fails with "Failed to fetch".</summary>

Either nothing answers on the ports in your configuration, or the TLS
certificate is expired. The server logs an error at start-up when the proxy
certificate has expired and warns two weeks ahead; the dashboard shows the
same warning. Renew the certificate (the docker-sealskin installer can renew
a Duck DNS one) and restart the container.
</details>

<details>
<summary>Firefox never connects, Chrome works.</summary>

Firefox does not allow extensions to talk to a server with an untrusted
certificate and has no HTTP fallback. Use a trusted certificate, for example
the Let's Encrypt one the installer obtains through Duck DNS, and make sure
the extension's server address is that hostname, not an IP.
</details>

<details>
<summary>The mobile app shows a blank page or "Server unreachable".</summary>

The Android and iOS WebViews reject self-signed certificates and block mixed
content, so the served UI only loads over HTTPS from a certificate the
device trusts. There is no fallback. Once the certificate is trusted, the app
behaves like the extension.
</details>

<details>
<summary>The server says "HOST_URL" in admin.json.</summary>

`HOST_URL` was not set when the container was first started. Open the file
and replace the string with your server's hostname or IP before importing
it, or set `HOST_URL` and delete the `keys/admins/admin` file and
`admin.json` to have them generated again (that also generates a new key).
</details>

<details>
<summary>I lost admin.json.</summary>

The server only keeps the public key. Remove `/config/.config/sealskin/keys/admins/admin`
and restart: a new `admin` key pair and `admin.json` are created. Any other
administrator can also create a new admin account from the dashboard.
</details>

<details>
<summary>The client says "Update required".</summary>

The extension or app speaks a different bridge protocol version than the UI
your server serves. Update whichever is older; the served UI is updated by
updating the server image.
</details>

## Sessions

<details>
<summary>A launch fails with a Docker error.</summary>

The server needs the Docker socket and the right to use it. If the error is
a permission denied on `/var/run/docker.sock`, make the socket group-writable
on the host (`sudo chmod g+rw /var/run/docker.sock`) and restart the
container. `docker logs sealskin` shows the exact error from Docker.
</details>

<details>
<summary>A launch times out waiting for the container.</summary>

The container started but did not answer HTTP within a minute. Usually the
image is still being pulled for the first time (pull it from **Installed
Apps** ahead of time), the host is out of memory, or the server cannot reach
the container's IP. The server and its sessions must share a Docker network;
the server uses the network it finds itself attached to.
</details>

<details>
<summary>"Nvidia runtime error on host."</summary>

Install the `nvidia-container-toolkit` and register it with Docker. NVIDIA
support needs the proprietary driver 580 or newer with
`nvidia-drm.modeset=1`; on a headless host run `nvidia-modprobe --modeset`
once per boot so `/dev/nvidia-modeset` exists before a session starts.
</details>

<details>
<summary>No GPU is offered in the launcher.</summary>

Three things have to line up: the server detected the GPU at start-up (the
dashboard lists detected GPUs), the user's settings allow GPU access, and
the application declares support for that GPU type (`nvidia_support` or
`dri3_support` in the store entry). Restart the container after installing
drivers so detection runs again.
</details>

<details>
<summary>Files I put in the session are gone after it stops.</summary>

Cleanroom sessions are deleted with the session. Use a home directory (or
**Auto**) and put files in `Desktop/files`, which is your shared files
directory and survives every session. Sending a file to a running session
from the launcher also places it there.
</details>

<details>
<summary>The session opens but the clock is wrong.</summary>

The client sends your browser's time zone with every launch and the
container's `TZ` follows it; failing that, the server's own `TZ` applies. Set
`TZ` on the sealskin container for a sensible default.
</details>

## Server

<details>
<summary>Can I run SealSkin behind my existing reverse proxy?</summary>

It is not designed for it. Caddy inside the container terminates TLS,
authenticates every session request with `forward_auth` and proxies
WebSockets to the containers; another proxy in front has to pass all of that
through untouched, including the `Upgrade` headers and the cookies scoped to
each session path. Exposing the session port directly is the supported
setup.
</details>

<details>
<summary>Why must the container be named sealskin?</summary>

The server inspects its own container to translate the paths it sees
(`/config`, `/storage`) into the host paths Docker needs for session
volumes, to learn the externally mapped ports it writes into configuration
files, and to attach sessions to its network. It looks itself up by that
name, falling back to its hostname.
</details>

<details>
<summary>I edited a YAML file by hand and nothing happened.</summary>

The watcher reloads a file when it changes on disk, with a short debounce.
Check the log for a parse error: a broken file is skipped with a message and
the previous state kept. Files whose name starts with `.` are ignored, and
`SEALSKIN_WATCH_CONFIG_FILES=false` disables the watcher altogether, in
which case a restart picks up the edit.
</details>

<details>
<summary>How do I move to a new server key or a new certificate?</summary>

Replace `ssl/proxy_cert.pem` and `ssl/proxy_key.pem` and restart for a new
certificate; clients need nothing. Replacing `ssl/server_key.pem` changes
the server public key every client has stored, so every user has to import
a new configuration file (the **Export Config** of an already-updated client
or a new file from the Users panel).
</details>

<details>
<summary>Where are the logs?</summary>

`docker logs -f sealskin`. `SEALSKIN_LOG_LEVEL=DEBUG` makes the server
considerably more talkative. Caddy logs errors only.
</details>

## Getting help

Search the [issues](https://github.com/selkies-project/sealskin/issues) and
ask on the [LinuxServer.io Discord](https://discord.com/invite/linuxserver).
Problems with a specific application image belong with that image's
repository; problems with the container packaging belong with
[docker-sealskin](https://github.com/linuxserver/docker-sealskin).
