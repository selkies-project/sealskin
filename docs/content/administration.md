---
title: Administration
description: Users, groups, administrators, app stores, installed applications, templates, the App Laboratory, sessions, and GPUs.
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

**Create New User** takes a username (letters, digits, `_`, and `-`). In the
web app that is all: the user signs in through the
[identity provider or proxy](signin.md), and one who signs in before being
created is created then, with the default settings. In the extension and the
mobile app the form also takes a public key: leave it blank and the server
generates an RSA key pair, shows the resulting configuration file **once**,
and forgets the private key; paste a key when the user generated their own
pair on the connection page and sent you the public half.

Each user carries these settings, editable later:

| Setting | Effect |
| --- | --- |
| **Active Account** | An inactive user is refused. |
| **Groups** | The groups the user is in, beside those the identity provider puts them in, which the form lists. |
| **Administrator** | The user administers the server. |
| **Allow Persistent Storage** | Without it every session is a cleanroom, the file manager is unavailable, and files cannot be sent to sessions. |
| **Allow Public File Sharing** | Enables share links from the file manager. Requires persistent storage. |
| **Allow GPU Access** | Whether the launcher offers GPUs to this user. |
| **Share GPUs** | Off gives each of the user's GPU sessions a GPU no other session is on. |
| **Move Home Directories** | The user may move their own home directories between the nodes of a [cluster](cluster.md). |
| **Allow Editing App Templates** | Opens the [App Templates](#app-templates) editor to a user who is not an administrator. They create, change, and delete templates, except for the `DOCKER_*` settings, which grant authority over the Docker host: those keep the values an administrator gave them, and only an administrator deletes a template that carries any. |
| **Harden Container**, **Harden Window Manager** | Force the base image presets `HARDEN_DESKTOP` and `HARDEN_OPENBOX` on every session the user starts, including apps a collaboration room swaps to. They are applied after the [app template](#app-templates) and the app's own environment overrides, so neither can switch them back off. Leave them off to let the template decide. |
| **Limits** | Sessions at once, storage, CPUs and memory per session, session length, and a time allowance per day, week, or month; see [the limits](signin.md#groups-switches-and-limits). Blank or negative is no limit. |
| **Pools** | Restricted [pools](cluster.md#pools) open to the user, and pools closed to them. |
| **PRoot Apps catalog** | The [catalog](#proot-apps) the user's sessions install PRoot Apps from. Left at **None**, the first of the user's groups to choose one decides. |

Deleting a user also deletes their storage on the node the dashboard is
served from. A user with no key shows **Single sign-on only** in place of a
public key.

**Manage Home Directories** in a user's row lists, creates, deletes, and, on a
cluster, moves home directories on their behalf.

## Groups

A group sets some of the same switches and limits for its members and leaves
the rest alone: each switch is **Not set**, **Allow**, or **Deny**, and a
blank limit is not set. A user in one group gets the group's value for
everything the group sets. A user in several gets, for each setting, the
restricting value if any of the groups gives it, and the smallest limit; the
edit dialog shows the result as **Effective Settings**. The **PRoot Apps
catalog** is a choice rather than a limit: a member's own choice wins, then
the first of their groups that makes one. **Identity provider
groups** names the provider's groups whose members are in this group without
being listed. Groups are also a permission target for applications and
pools. Deleting a group leaves its members with what their other groups and
their own settings give.

## Cluster

Shown to administrators; [Clusters](cluster.md) explains each part.

* **Nodes**: every node with its roles, pool, load, and sessions, whether it
  is answering, and the controls to approve, suspend, move to a pool, or
  remove it. **Add a node** issues a join code.
* **Pools**: who may use each pool and what an hour in it costs.
* **Store**: where the shared records are kept and whether it answers.
* **Usage**: the weighted hours each user spent this day, week, or month.

## Branding

The web app can wear your organization's name instead of SealSkin's. The
panel is a form beside a mock of the home page, which repaints as you type;
nothing changes until **Save branding**, and **Back to defaults** removes it
all.

* **Product name**: stands wherever the interface would say SealSkin, in
  every language: the page title, the sidebar, the sign-in page, the
  installed web app, and the browser's search engine entry.
* **Logo**: a square PNG, JPEG, or WebP up to 1 MB. It is the sidebar logo,
  the favicon, the icon of the installed web app, and the picture of an
  application that has none. **The logo links to** an address of your own,
  opened in a new tab; empty, the logo links nowhere. By default it leads to
  the SealSkin project on GitHub.
* **Accent color**: one color, from which the buttons, highlights, focus
  rings, and the wordmark take their shades. Clear it for the default violet.
* The **extension and app store links** on the sign-in page can be hidden.
* **Wallpaper**: a picture behind the home page, up to 8 MB (a larger one is
  scaled down in the browser before it is uploaded), with the same position,
  blur, and dim controls users have. Users start from it and may still pick
  their own background, unless **Force this wallpaper on every user** is on,
  which also takes the Customize button off the home page.
* **Home page links**: tiles the home page shows above the applications, for
  your own sites and tools, in the order given. Each has a name, an address,
  an icon (a [Font Awesome](https://fontawesome.com/search?ic=free-collection) class such as `fa-book` or `fab fa-github`, or an
  `https` address of a picture), and opens either in a new tab or, marked
  **In isolation**, in a session of an application the user picks, as a
  pasted link does.

The brand is kept in `branding/` beside the other shared records, as
`branding.yml` with the logo and wallpaper files next to it, so every node of
a cluster serves it and a deployment can seed it from a volume (see
[Configuration](configuration.md)). The browser extension and the mobile app
show their own name until they connect; what they frame then is the branded
web app. With [auto sign-in](signin.md#landing-on-the-provider) on, the sign-in
page is the identity provider's own, so there is nothing of SealSkin's to
brand there.

## Sign In

One card for each [way of signing in](signin.md), written for the whole
cluster: OpenID Connect and SAML with the addresses to register at the
provider and a **Test** that asks the provider for its metadata and shows
what came back; the reverse proxy headers; **Landing**, which sends a
signed-out browser straight to a provider; and who a sign-in is (the username
and groups claims, the administrator group, and how long a sign-in lasts). A
field left empty is taken from each node's environment. The **Root Token**
card says whether this node still takes the token, and warns when it does not
and no identity provider group names administrators.

## Audit Log

What was done on every node, by whom, newest first: sign-ins, launches and
stops, and administrative changes. Search it, choose a day or a range, page
through it, and export everything that matches as CSV or JSON.

## Admins

Creates and deletes administrators, with the same key handling as users. The
panel also shows the **server public key** that every configuration file
carries; a user who configures the client by hand needs it.

A user an identity provider or a proxy signs in as an administrator, through
`SEALSKIN_SSO_ADMIN_GROUP` or a group with the **Administrator** switch, is
not listed here: they stay under [Users](#users), marked **Administrator**,
since the rights come from the provider at each sign-in.

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
* **Features**: GPU support, home directory mounting, URL opening, and file
  opening as declared by the store, which you can turn off for this install.
* **Auto Update Image**: include this image in the hourly pull.
* **Application Template**: the [template](#app-templates) whose environment
  is applied to every launch.
* **Environment variables**: extra `NAME=value` pairs passed to the container.

**Add Manual App** installs an image that is in no store. It must be built on
a Selkies-compatible base (the LinuxServer.io `baseimage-selkies`), listen on
the port you enter, and honour the same environment variables.

## Installed Apps

Every installed application with its source, image, and image status.
**Check** compares the local image digest with the registry; **Pull** fetches
the newest image and refreshes the app's cached autostart script. On
Kubernetes, where nodes pull images themselves, **Pull** pins the registry's
current digest for new sessions instead (see [Kubernetes](kubernetes.md#images)). **Edit**
reopens the install dialog. Deleting an app does not stop its running
sessions.

With `SEALSKIN_AUTO_UPDATE_APPS` enabled (the default) a background job pulls
every auto-updating app's image once an hour, refreshes the store caches, and
prunes dangling images. Running sessions keep their container; the new image
is used by the next launch.

Installed apps are stored as a **reference to the store entry plus your
overrides**, so when the store changes an image tag or an extension list, the
change applies at the next cache refresh without reinstalling. Only the
fields you changed stay pinned.

## PRoot Apps

[PRoot Apps](https://github.com/linuxserver/proot-apps) is the package
manager inside every LinuxServer.io desktop image: `proot-apps install
firefox` unpacks an application into the home directory, and its graphical
installer (`gui`) offers the same list with icons. A **catalog** is a folder
of those packages that SealSkin keeps on every node and mounts read-only into
sessions, so the users assigned to it install and update from the folder and
their sessions reach no registry: PRoot Apps finds the catalog through
`PA_REPO_FOLDER`, which the session's environment points at the mount.

**New Catalog** opens the editor. Give the catalog a name, then pick its apps
from the grid, which lists what the **remote** publishes. A remote is the
GitHub `owner/repo` of a proot-apps repository; the editor opens on
`SEALSKIN_PROOT_APPS_REMOTE` (linuxserver/proot-apps) and takes any fork
built the same way, whose apps are then installed by their full image name
(`ghcr.io/<owner>/<repo>:<app>`), since the short names belong to the default
remote. A catalog can mix apps from several remotes. Include `gui` to give
the users the graphical installer, which the remote lists as disabled: it is
installed like any app (`proot-apps install gui`) and shows the catalog,
nothing more.

The table shows the state of this node's copy of each catalog. Every node
with sessions fetches the apps itself, for its own architecture, as soon as
a catalog is saved; **Update** fetches again, on every node, the apps whose
package changed, which **Auto update** also does on the auto-update interval.
Apps a remote does not build for the node's architecture are skipped there
and hidden by the installer. Deleting a catalog removes every node's copy;
users assigned to it keep the setting, which then does nothing.

Assign a catalog to a user or a group in their settings. A session mounts its
catalog at `/mnt/proot-apps`, and a user with no catalog has PRoot Apps as
the image ships it, fetching from the registry. The catalogs live under
`SEALSKIN_PROOT_APPS_PATH` (`/storage/sealskin_proot_apps`), one folder per
catalog, in the layout the [proot-apps
README](https://github.com/linuxserver/proot-apps#for-administrators)
describes for a local repository: `metadata/` for the installer and one
`ghcr.io_<owner>_<repo>_<app>/` folder per package.

## App Templates

A template is a named set of environment variables applied to every session
of the apps that use it. The **Application Template Editor** shows the
variables as collapsible cards, each with a count of the values that differ
from the default; a box above filters them by name, and **Changed only**
leaves just the values a template sets. The cards:

* **Appearance**: the page title, logo, sidebar title, and dashboard style,
  then whether the Selkies sidebar shows and which of its sections and
  buttons it offers, with a preview that follows the toggles.
* **Display**: manual resolution, dynamic resize, aligned resolution,
  scaling and DPI, second screen, fullscreen, window decorations, and the
  Wayland backend.
* **Input**: keyboard shortcuts, cursors, pointer options, published input
  devices, and gamepads with their kernel devices.
* **Audio** and **Webcam**: enabling each, what starts at connect, and the
  encoding of each.
* **Performance**: encoders, frame rate, CRF and bitrate ranges, rate
  control, keyframes, JPEG and paint-over quality, backpressure, and the
  watermark on the stream.
* **GPU**: GPU selection, render nodes, DRI3, and CPU encoding.
* **Clipboard and Files**: clipboard policy and seamless sync, file
  transfers and their directory, printing, and the command websocket.
* **Sharing**: sharing links, view-only, collaboration, and players.
* **Hardening**: the presets behind the user-level hardening switches and
  their individual components.
* **Lifecycle**: connect and disconnect hooks, the application ready file,
  Docker-in-Docker, the Steam shim, and IPv6.
* **Integrations**: the audit webhook, the Computer-Use server, recording,
  metrics, and debugging.
* **WebRTC**: a block of its own, since its values are one decision: the
  base image streams over WebSockets until the mode says WebRTC or a STUN,
  TURN, Cloudflare, or public IP value is set, and then streams over WebRTC
  with dual mode on. The card's badge names which the template amounts to.
  Inside it: mode and dual mode, transport (pacing, congestion control,
  ICE-lite, ports), STUN, TURN credentials, the TURN REST API, Cloudflare
  TURN, and diagnostics.
* **Container**: `DOCKER_*` settings that become container run options rather
  than environment variables: privileged mode, capabilities, devices, extra
  bind mounts, memory and CPU limits, network, IPC and PID modes, DNS,
  sysctls, ulimits, tmpfs, and extra groups. Only an administrator edits
  these.

The list of variables comes from `template_schema.yml` on the server, so a
new image option is a server change and never needs a client update. The
per-app environment overrides from the install dialog are applied after the
template, so the more specific setting wins; the user-level hardening
switches are applied after both.

Templates written before the Selkies variables were renamed keep working:
`SELKIES_H264_*` keys, `SELKIES_IS_MANUAL_RESOLUTION_MODE`, the three
`SELKIES_CLIPBOARD_*` booleans, and the `x264enc` encoder spellings are
translated to their current names when the template is loaded, and saving the
template from the editor writes the current names.

A blank **Default** template is created on first start. Templates shipped in
the default templates directory cannot be deleted from the UI.

## App Laboratory

The laboratory builds a **meta-app**: a variant of an installed app with its
own name, icon, autostart script, and, most usefully, a pre-populated home
directory. Choose the base app, name the new one, upload an icon, optionally
paste an autostart script (the Wayland and X11 variants are separate), and
pick who may use it, then **Save & Launch Customization Session**.

That session runs the base app with the meta-app's **home template** mounted
read-write. Configure the application, sign in to services, place files,
adjust settings; when you are done, **Close Session & Finalize** stops it and
keeps the directory as the template. Reopening the laboratory on an existing
meta-app launches it the same way for further changes.

In the web app the customization session opens in a tab of its own, and the
laboratory keeps track of it: while one is open the page shows it first, with
**Re-open session** and **Close & save template**, wherever you have been in
between. An administrator has one open at a time. It is not a session in the
ordinary sense: it appears in no session list, only here, and counts toward
no limit. It runs on the node the dashboard is served from, where the
template is kept.

For users, a meta-app behaves like any other app, except that the first
launch copies the template into their `auto-<name>` home directory, and a
cleanroom launch copies it into the ephemeral one. Deleting a meta-app
removes its icon and template.

## Sessions

**Active Sessions** lists every user's sessions with the application, start
time, and launch context, and can stop any of them. Users see only their own.

## GPUs

At start-up the server detects GPUs on the host:

* **NVIDIA** cards through the NVIDIA driver. Sessions get the NVIDIA
  container runtime with all capabilities and, when it exists,
  `/dev/nvidia-modeset`. This needs the proprietary driver 580 or newer, the
  `nvidia-container-toolkit` v1.20.1 or higher, whose refresh service creates
  that node at boot, and the kernel parameter `nvidia-drm.modeset=1`.
* **DRI3** devices (Intel, AMD, and others) through `/dev/dri`. The render
  node is passed into the container and exported as `DRI_NODE`.

A launch may use a GPU when the user's settings allow it and the app declares
support for that GPU type. On Wayland, NVIDIA sessions also receive the DRI
node so the compositor can use the card directly. On Kubernetes the GPUs are
the requests sessions can make, detected or defined as PodTemplates, as
[Kubernetes](kubernetes.md#gpus) describes.

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
