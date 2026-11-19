---
title: Podman
description: Run the server and its sessions on Podman, rootful or rootless, including SteamOS, which ships Podman and no Docker.
---

# Podman

Podman serves Docker's API on a socket of its own, and the server talks to it
the way it talks to Docker: the socket is mounted at `/var/run/docker.sock`
and every session is a container the server starts through it. Three things
differ from Docker, and the server handles the first two itself:

* **Its own container.** Podman marks a container with `/run/.containerenv`
  and keeps its files under `overlay-containers/<id>`, which the server reads
  to tell its own container from another one under the same name.
* **NVIDIA GPUs** are CDI devices. Podman accepts Docker's `nvidia` runtime
  and GPU requests without an error and starts the container without a GPU,
  so under Podman the server asks for `nvidia.com/gpu=<index>` instead. That
  device exists once the host has a CDI spec for its NVIDIA GPUs, which
  `nvidia-ctk` from the `nvidia-container-toolkit` writes:

  ```sh
  sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
  ```

  The spec names the driver's files by version, so it is written again after
  a driver update, unless the toolkit's `nvidia-cdi-refresh` service does that
  at boot. A launch on a host without one fails with a message naming the
  command. Intel and AMD GPUs need nothing: their render node is passed in as
  under Docker.
* **The network.** The server reaches each session at the session's address on
  the server's own network. Rootful Podman puts a container on its `podman`
  bridge, which sessions join too. Rootless Podman's default, `pasta`, puts it
  on no network another container can join, so a rootless server runs on a
  network made for it; one without logs an error at startup saying so.

## Rootful, with Quadlet

Podman's systemd integration runs the server as a service from two files in
`/etc/containers/systemd`. Enable the socket first:

```sh
sudo systemctl enable --now podman.socket
```

`/etc/containers/systemd/sealskin.network`:

```ini
[Network]
NetworkName=sealskin
```

`/etc/containers/systemd/sealskin.container`:

```ini
[Unit]
Description=SealSkin
Requires=podman.socket
After=podman.socket network-online.target
Wants=network-online.target

[Container]
Image=lscr.io/linuxserver/sealskin:latest
ContainerName=sealskin
HostName=sealskin
Network=sealskin.network
Environment=PUID=1000 PGID=1000 TZ=Etc/UTC
Environment=HOST_URL=sealskin.example.com
Volume=/path/to/sealskin/config:/config
Volume=/path/to/sealskin/storage:/storage
Volume=/run/podman/podman.sock:/var/run/docker.sock
PublishPort=8443:8443
PublishPort=8000:8000

[Service]
Restart=always
# The first start pulls the image.
TimeoutStartSec=900

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl daemon-reload && sudo systemctl start sealskin`. The
rules [Getting started](start.md#with-docker-compose) gives for the compose
file hold here too: the container keeps its name, `PUID` and `PGID` own
`/config` and `/storage`, and `HOST_URL` goes into the admin configuration
file. The same settings make `podman run`:

```sh
sudo podman network create sealskin
sudo podman run -d --name sealskin --hostname sealskin --network sealskin \
  -e PUID=1000 -e PGID=1000 -e TZ=Etc/UTC -e HOST_URL=sealskin.example.com \
  -v /path/to/sealskin/config:/config -v /path/to/sealskin/storage:/storage \
  -v /run/podman/podman.sock:/var/run/docker.sock \
  -p 8443:8443 -p 8000:8000 lscr.io/linuxserver/sealskin:latest
```

On a host that enforces SELinux, such as Fedora or RHEL, the label Podman
gives a container is expected to keep it from the socket mounted into it,
which `--security-opt label=disable` on the server's container, or
`SecurityLabelDisable=true` in its Quadlet file, lifts. Neither setup on this
page has been tried on such a host.

## Rootless

A user's own Podman works too, with its socket and a network of its own:

```sh
systemctl --user enable --now podman.socket
podman network create sealskin
podman run -d --name sealskin --hostname sealskin --network sealskin \
  -e PUID=1000 -e PGID=1000 -e TZ=Etc/UTC -e HOST_URL=sealskin.example.com \
  -v ~/sealskin/config:/config -v ~/sealskin/storage:/storage \
  -v /run/user/$(id -u)/podman/podman.sock:/var/run/docker.sock \
  -p 8443:8443 -p 8000:8000 lscr.io/linuxserver/sealskin:latest
```

The same pair of files under `~/.config/containers/systemd` runs it as a user
service, with the socket at `%t/podman/podman.sock`; `loginctl enable-linger`
keeps the user's services running when no one is signed in. Two limits come
with running rootless:

* A rootless session uses a GPU with the user's rights, so it can use one only
  where the render node is open to it. SteamOS opens it to all; on other GPU
  hosts the rootful setup is the one known to work.
* The files the server and its sessions write are owned by the user's
  subordinate IDs, so the user removes them with `podman unshare rm -rf`.

## SteamOS

SteamOS ships Podman and no Docker. The rootless setup above is the one that
has run there. Its root filesystem is read-only and replaced on each update,
while `/etc` and `/home` are kept, so the rootful setup keeps everything that
lasts in those two places. It follows the layout of a running SteamOS node
but has not been tried there, nor has the `rootless_storage_path` line below:

* The Quadlet files and the socket's enablement live in `/etc`.
* Podman keeps its images and containers under `/var` by default, which an
  update does not keep. `/etc/containers/storage.conf` moves them to `/home`:

  ```ini
  [storage]
  driver = "overlay"
  graphroot = "/home/.containers/storage"
  runroot = "/run/containers/storage"
  rootless_storage_path = "$HOME/.local/share/containers/storage"
  ```

  Without `rootless_storage_path`, a user's own Podman takes the system
  `graphroot` too and cannot start.
* `/config` and `/storage` are directories under `/home`.

The render node of the APU is open to all on SteamOS, so a rootless server
there gives its sessions the GPU as well.
