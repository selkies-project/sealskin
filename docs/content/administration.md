---
title: Administration
description: Users, groups, administrators, app stores, installed applications, templates, the App Laboratory, sessions and GPUs.
---

Administrators manage the server from the same options page every user sees,
which grows the panels below. All of it is also plain YAML and text under
`/config` (see [Configuration](configuration.md)); the panels and the files
are two views of the same data, and hand edits are picked up live.

There are two roles. **Administrators** can do everything, have no per-user
settings (they always get the defaults, with storage and GPU enabled) and
cannot be limited. **Users** launch and manage their own sessions and files
within the permissions set for them. The root account `admin` cannot be
deleted.

## Dashboard

The landing panel shows who you are, the server address, CPU model, storage
use, the GPUs the server detected, and a warning when the proxy certificate
expires within 14 days or has expired. **Export Config** produces the same
JSON file you imported, for setting up another device; **Logout & Clear
Config** wipes the client.

## Users

**Create New User** takes a username (letters, digits, `_` and `-`) and,
optionally, a public key. Leave the key blank and the server generates an RSA
key pair, shows the resulting configuration file **once** and forgets the
private key; hand that file to the user. Paste a key when the user generated
their own pair on the connection page and sent you the public half.

Each user carries these settings, editable later:

| Setting | Effect |
| --- | --- |
| **Active Account** | An inactive user's tokens are refused. |
| **Group** | Applies the group's settings on top of the user's own (see below). |
| **Allow Persistent Storage** | Without it every session is a cleanroom, the file manager is unavailable and files cannot be sent to sessions. |
| **Allow Public File Sharing** | Enables share links from the file manager. Requires persistent storage. |
| **Allow GPU Access** | Whether the launcher offers GPUs to this user. |
| **Harden Container**, **Harden Window Manager** | Force the base image presets `HARDEN_DESKTOP` and `HARDEN_OPENBOX` on every session the user starts, including apps a collaboration room swaps to. They are applied after the [app template](#app-templates) and the app's own environment overrides, so neither can switch them back off. Leave them off to let the template decide. |
| **Sessions limit**, **Storage limit** | Recorded but **not enforced yet**; `-1` means unlimited once they are. |

Deleting a user also deletes their storage.

**Manage Home Directories** in a user's row lists, creates and deletes home
directories on their behalf.

## Groups

A group is a named set of the same settings. A user assigned to a group gets
the group's values **in place of** their own for every setting the group
defines, which the edit dialog shows as **Effective Settings**. Groups are
also a permission target for applications: an app can be limited to the
members of certain groups. Deleting a group reverts its members to their
individual settings.

## Admins

Creates and deletes administrators, with the same key handling as users. The
panel also shows the **server public key** that every configuration file
carries; a user who configures the client by hand needs it.

## App Stores

An app store is a YAML catalogue at a URL. The default store is
[linuxserver/sealskin-apps](https://github.com/linuxserver/sealskin-apps),
which covers the LinuxServer.io desktop images. **Add New App Store** takes a
name and a URL; the server fetches and caches the file and refreshes it on
the auto-update interval or on demand with **Refresh**. The format is
described in [Configuration](configuration.md#app-store-catalogues).

**Available Apps** shows the selected store. Installing opens the app's
settings:

* **Custom Name** and **Container Image**: override what the store says. An
  image you change here is kept even when the store entry is updated.
* **Allowed Users** and **Allowed Groups**: comma-separated names, or `all`.
  An app is visible to a user when they are listed, their group is listed, or
  either list says `all`.
* **Features**: GPU support, home directory mounting, URL opening and file
  opening as declared by the store, which you can turn off for this install.
* **Auto Update Image**: include this image in the hourly pull.
* **Application Template**: the [template](#app-templates) whose environment
  is applied to every launch.
* **Environment variables**: extra `NAME=value` pairs passed to the container.

**Add Manual App** installs an image that is in no store. It must be built on
a Selkies-compatible base (the LinuxServer.io `baseimage-selkies`), listen on
the port you enter and honour the same environment variables.

## Installed Apps

Every installed application with its source, image and image status.
**Check** compares the local image digest with the registry; **Pull** fetches
the newest image and refreshes the app's cached autostart script. **Edit**
reopens the install dialog. Deleting an app does not stop its running
sessions.

With `SEALSKIN_AUTO_UPDATE_APPS` enabled (the default) a background job pulls
every auto-updating app's image once an hour, refreshes the store caches and
prunes dangling images. Running sessions keep their container; the new image
is used by the next launch.

Installed apps are stored as a **reference to the store entry plus your
overrides**, so when the store changes an image tag or an extension list, the
change applies at the next cache refresh without reinstalling. Only the
fields you changed stay pinned.

## App Templates

A template is a named set of environment variables applied to every session
of the apps that use it. The **Application Template Editor** groups the
variables by category:

* **UI**: the Selkies sidebar, its sections, the page title, watermark and
  dashboard style.
* **App**: audio, microphone, clipboard policy, gamepads, file transfers,
  sharing links, second screen, cursor handling, resolution and scaling, then
  the audio and video encoding controls: encoders, frame rate, CRF and
  bitrate ranges, rate control, keyframes, paint-over quality and the virtual
  webcam.
* **General**: resolution limits, Docker-in-Docker, IPv6, DRI3 and Zink, GPU
  selection, window decorations, gamepad and webcam injection, connect and
  disconnect hooks, debugging.
* **Hardening**: the presets behind the user-level hardening switches and
  their individual components.
* **WebRTC**: streaming mode, dual mode, pacing and congestion control, and
  the STUN, TURN, TURN REST and Cloudflare TURN credentials. The base image
  streams over WebSockets until one of these is set; any STUN, TURN,
  Cloudflare or public IP value switches the session to WebRTC with dual mode
  on.
* **Docker**: `DOCKER_*` settings that become container run options rather
  than environment variables: privileged mode, capabilities, devices, extra
  bind mounts, memory and CPU limits, network, IPC and PID modes, DNS,
  sysctls, ulimits, tmpfs and extra groups.

The list of variables comes from `template_schema.yml` on the server, so a
new image option is a server change and never needs a client update. The
per-app environment overrides from the install dialog are applied after the
template, so the more specific setting wins; the user-level hardening
switches are applied after both.

Templates written before the Selkies variables were renamed keep working:
`SELKIES_H264_*` keys, `SELKIES_IS_MANUAL_RESOLUTION_MODE`, the three
`SELKIES_CLIPBOARD_*` booleans and the `x264enc` encoder spellings are
translated to their current names when the template is loaded, and saving the
template from the editor writes the current names.

A blank **Default** template is created on first start. Templates shipped in
the default templates directory cannot be deleted from the UI.

## App Laboratory

The laboratory builds a **meta-app**: a variant of an installed app with its
own name, icon, autostart script and, most usefully, a pre-populated home
directory. Choose the base app, name the new one, upload an icon, optionally
paste an autostart script (the Wayland and X11 variants are separate), and
pick who may use it, then **Save & Launch Customization Session**.

That session runs the base app with the meta-app's **home template** mounted
read-write. Configure the application, sign in to services, place files,
adjust settings; when you are done, **Close Session & Finalize** stops it and
keeps the directory as the template. Reopening the laboratory on an existing
meta-app launches it the same way for further changes.

For users, a meta-app behaves like any other app, except that the first
launch copies the template into their `auto-<name>` home directory, and a
cleanroom launch copies it into the ephemeral one. Deleting a meta-app
removes its icon and template.

## Sessions

**Active Sessions** lists every user's sessions with the application, start
time and launch context, and can stop any of them. Users see only their own.

## GPUs

At start-up the server detects GPUs on the host:

* **NVIDIA** cards through the NVIDIA driver. Sessions get the NVIDIA
  container runtime with all capabilities and, when it exists,
  `/dev/nvidia-modeset`. This needs the proprietary driver 580 or newer, the
  `nvidia-container-toolkit`, the kernel parameter `nvidia-drm.modeset=1` and,
  on a headless host, `nvidia-modprobe --modeset` once per boot so the device
  exists before the first container starts.
* **DRI3** devices (Intel, AMD and others) through `/dev/dri`. The render
  node is passed into the container and exported as `DRI_NODE`.

A launch may use a GPU when the user's settings allow it and the app declares
support for that GPU type. On Wayland, NVIDIA sessions also receive the DRI
node so the compositor can use the card directly.

## Keys and certificates

The dashboard warns about a proxy certificate that is expired or about to
expire. Replacing `ssl/proxy_cert.pem` and `ssl/proxy_key.pem` under
`/config` and restarting the container installs a new one; the installer
script from docker-sealskin can renew a Duck DNS certificate and do the
restart for you.

The server key `ssl/server_key.pem` is what every client's stored **server
public key** verifies. Replacing it invalidates every client configuration,
so treat it like the CA of your installation. Rotating a **user's** key means
creating a new user file or editing the public key in it; see
[Configuration](configuration.md#users-administrators-and-groups).
