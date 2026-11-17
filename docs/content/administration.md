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
within the permissions set for them. The built-in administrator `admin`,
who signs in with the root key on the web and with the first start's key
file in the apps, cannot be deleted.

## Dashboard

The landing panel shows who you are, the server address, CPU model, storage
use, the GPUs the server detected, and a warning when the proxy certificate
expires within 14 days or has expired. **Export Config** produces the same
JSON file you imported, for setting up another device; **Logout & Clear
Config** wipes the client.

## Users

**Create New User** takes a username (letters, digits, `_`, and `-`). In the
web app, and wherever key files are off, that is all: the user signs in
through the [identity provider or proxy](signin.md), and one who signs in
before being created is created then, with the default settings; creating
the name first is for a provider that is not to create accounts
(`SEALSKIN_SSO_CREATE_USERS=false`). With key files on, in the extension and
the mobile app the form also takes a public key: leave it blank and the server
generates an RSA key pair, shows the resulting configuration file **once**,
and forgets the private key; paste a key when the user generated their own
pair on the connection page and sent you the public half. The Admins tab,
which makes key-file administrators, is there while key files are on;
otherwise administrators come from the provider's group or a group's
**Administrator** switch.

Each user carries these settings, editable later:

| Setting | Effect |
| --- | --- |
| **Active Account** | An inactive user is refused. |
| **Groups** | The groups the user is in, beside those the identity provider puts them in, which the form lists. In none, the user is in `default` (see [Groups](#groups)). |
| **Administrator** | The user administers the server. |
| **Allow Persistent Storage** | Without it every session is a cleanroom, the file manager is unavailable, and files cannot be sent to sessions. |
| **Allow Public File Sharing** | Enables share links from the file manager. Requires persistent storage. |
| **Allow GPU Access** | Whether the launcher offers GPUs to this user. |
| **Share GPUs** | Off gives each of the user's GPU sessions a GPU no other session is on. |
| **Move Home Directories** | The user may move their own home directories between the nodes of a [cluster](cluster.md), from the launcher. On by default: a user who has home directories can take them where they launch. |
| **Allow Editing App Templates** | Opens the [App Templates](#app-templates) editor to a user who is not an administrator. They create, change, and delete templates, except for the `DOCKER_*` and `KUBERNETES_*` settings, which grant authority over the Docker host or the namespace: those keep the values an administrator gave them, and only an administrator deletes a template that carries any. The same holds for the session's user, password, and Selkies tokens, and for the variables a container runs or loads code from as it starts (`S6_*`, `LD_*`, `FILE__*`, `BASH_ENV`, `ENV`, `PATH`, `HOME`, `IFS`). |
| **Harden Container**, **Harden Window Manager** | Force the base image presets `HARDEN_DESKTOP` and `HARDEN_OPENBOX` on every session the user starts, including apps a collaboration room swaps to. They are applied after the [app template](#app-templates) and the app's own environment overrides, so neither can switch them back off. Leave them off to let the template decide. |
| **Allow Customizing the Home Page**, **Allow Own Links**, **Allow Own Feeds and Calendars** | What the user may do with their [dashboard](dashboards.md): rearrange it and add to it at all, add links of their own, and add feeds and calendars by address, which the server fetches from the public internet. All on by default. |
| **Limits** | Sessions at once, storage, CPUs and memory per session, session length, and a time allowance per day, week, or month; see [the limits](signin.md#groups-switches-and-limits). Blank or negative is no limit. |
| **Application keys** | Whether the user may sign the extension and the mobile app in with [application keys](signin.md#application-keys), which apps may use them, how long a key lives, and the dormancy rules that send the user back to the web to sign in. These bind administrators too. |
| **Pools** | Restricted [pools](cluster.md#pools) open to the user, and pools closed to them. |
| **PRoot Apps catalog** | The [catalog](#proot-apps) the user's sessions install PRoot Apps from. Left at **None**, the first of the user's groups to choose one decides. |

Deleting a user also deletes their storage on the node the dashboard is
served from. A user with no key shows **Single sign-on only** in place of a
public key.

**Manage Home Directories** in a user's row lists, creates, deletes, and, on a
cluster, moves home directories on their behalf. **Keys** lists the user's
application keys, with the app each was last used from, and revokes one or
all of them; the user makes new ones under **Application Keys** in Settings.

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
being listed. Groups are also a permission target for applications, VPN
profiles, and pools. Deleting a group leaves its members with what their other
groups and their own settings give.

The table opens with `default`, marked **Built in**: the group of every user in
no other group, with no file behind it. **View** shows its settings, which are
the server's defaults; they cannot be changed here, and no group may take the
name. To change what a user in `default` gets, place them in a group or edit
the user. Naming `default` in the users and groups of an application, a VPN
profile, or a pool opens it to exactly those users, where `all` opens it to
everyone.

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
* **Companion apps**: where users get the Chrome extension, the Firefox
  add-on, the Android app, and the iOS app, linked from the sign-in page and
  beside the application keys in the profile. Each link can point at a build
  of your own, or be hidden, as when an organization ships one of them only.
* **Wallpaper**: a picture behind the home page, up to 8 MB (a larger one is
  scaled down in the browser before it is uploaded), with the same position,
  blur, and dim controls users have. Users start from it and may still pick
  their own background, unless **Force this wallpaper on every user** is on,
  which also takes the Appearance button out of the home page's edit mode.
* **Home page links**: tiles the home page shows above the applications, for
  your own sites and tools, in the order given. Each has a name, an address,
  an icon (a [Font Awesome](https://fontawesome.com/search?ic=free-collection) class such as `fa-book` or `fab fa-github`, or an
  `https` address of a picture, which the server fetches and serves itself),
  and opens either in a new tab or, marked
  **In isolation**, in a session of an application the user picks, as a
  pasted link does. They show on the built-in home page; a
  [dashboard](dashboards.md) carries links of its own.

### A brand per host name or group

**Brand** at the top of the form chooses which brand the fields below are:
the one everyone sees, or one of its own for some users, added with the
plus button. Such a brand has a label, the **host names** the web app is
reached at under it (`portal.example.org`, or `*.example.org` for every name
under one), the **groups** whose members see it once signed in on the web,
or both, and its own name, logo, accent color, wallpaper, and home page
links. The first brand in the list that matches a request is worn. The
sign-in page, the installed web app, and the search engine entry wear the
brand of the host name, since nobody is signed in there; a group's brand
shows after the sign-in. Until a brand has a logo or a wallpaper of its
own, the default's stands in. The companion apps' links are the same under
every brand, and the pages the extension and the mobile app frame wear the
brand of their host name.

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
described in [Configuration](configuration.md#app-store-catalogues), and a
host that asks for a sign-in is served by the node's
[netrc file](registries.md#hosts).

**Available Apps** shows the selected store. Installing opens the app's
settings:

* **Custom Name** and **Container Image**: override what the store says. An
  image you change here is kept even when the store entry is updated.
* **Icon**: the store's, or a PNG, JPEG, WebP, GIF, BMP, or ICO picture of up
  to 4 MB you upload, kept as a PNG of at most 512 pixels. **Reset** goes back
  to the store's icon, or to none for a manual app.
* **Allowed Users** and **Allowed Groups**: comma-separated names, or `all`.
  An app is visible to a user when they are listed, their group is listed, or
  either list says `all`.
* **Features**: GPU support, home directory mounting, URL opening, and file
  opening as declared by the store, which you can turn off for this install.
* **Auto Update Image**: include this image in the hourly pull.
* **Application Template**: the [template](#app-templates) whose environment
  is applied to every launch.
* **Environment variables**: extra `NAME=value` pairs passed to the container.

**Add Manual App** installs an image that is in no store, with the same
settings and an icon you upload. It must be built on
a Selkies-compatible base (the LinuxServer.io `baseimage-selkies`), listen on
the port you enter, and honour the same environment variables.

## Installed Apps

Every installed application with its source, image, and image status.
**Check** compares the local image digest with the registry; **Pull** fetches
the newest image and refreshes the app's cached autostart script. On
Kubernetes, where nodes pull images themselves, **Pull** pins the registry's
current digest for new sessions instead (see [Kubernetes](kubernetes.md#images)). **Edit**
reopens the install dialog. Deleting an app removes its uploaded icon and does
not stop its running sessions. A private registry, and a mirror every image is pulled through,
are set up on the node ([Registries and proxies](registries.md)).

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
GitHub `owner/repo` of a proot-apps repository, or the base URL of one
served elsewhere and the registry of its images, one space apart
([Registries and proxies](registries.md#proot-apps-remotes)); the editor
opens on `SEALSKIN_PROOT_APPS_REMOTE` (linuxserver/proot-apps) and takes any
repository built the same way. The apps of `SEALSKIN_PROOT_APPS_REMOTE` keep
their short names: a session on a catalog gets `PA_DEFAULT_REPO`, the
registry of that remote, which tells PRoot Apps what `install <app>` means.
Apps of any other remote are installed by their full image name
(`registry.example/it/proot-apps:<app>`). A catalog can mix apps from several
remotes. A PRoot Apps older than `PA_DEFAULT_REPO` takes short names for apps
of linuxserver/proot-apps whatever the setting says, so with such a desktop
image only that remote may be the setting. Apps the
remote marks `disabled` are not listed.

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
folder per package, named after its image (`ghcr.io_<owner>_<repo>_<app>/`).

## VPN Profiles

A profile is a provider's WireGuard or OpenVPN configuration, imported from
one config file or a zip of thousands, reduced to a template and a list of
endpoints. A session that picks a profile leaves through a gateway container
the node runs for that profile and endpoint, shared by every session that
picked them, and the application image needs nothing for it. Import a
profile, publish the endpoints users may pick, cap the connections it may
spend, and say who may use it; [VPN profiles](vpn.md) describes the import,
what the providers hand out, and how the routing works.

## Browser Policies

A browser policy is a named set of the settings Chromium and Firefox take from
an administrator: a home page, managed bookmarks, allowed and blocked sites,
forced extensions, trusted certificates, and every other policy the two
engines read. It is optional, like an application template: an application
names at most one, picked in its install dialog; a meta-app or a baked application
starts with the policy of the application it was made from. Every session of
the application starts with the policy in force, in a single-browser image and
in a full desktop alike.

The editor has a tab per engine, and a policy may fill in either or both.
Each tab lists the **Common** settings first and whatever else the policy
sets under them; the search and the group filter reach every policy the
engine reads on Linux. Ticking a policy puts
it in and shows a control for its value. A deprecated policy is listed only
while the policy sets it, or with **Show deprecated** ticked. On the Chromium tab **Users may
change this** applies a setting as a default instead of enforcing it. **JSON**
shows a tab as the file a browser reads and takes a pasted policy file, so an
existing `policies.json` or Chromium policy file is carried over whole. A
policy the catalog does not know, as one only Edge or Brave reads, is kept as
the JSON it was given. `CloudManagementEnrollmentToken` is refused: the shared
record holds no credential.

The names, descriptions, and value shapes come from the engines' own policy
definitions, read at a pinned revision by
`server/scripts/ingest_browser_policies.py` into the two catalogs under
`server/app/browser_policy_catalog/`; running the script again refreshes them.
The titles and descriptions are shown in the language of the web UI where the
engine publishes a translation (Chromium in 13 of the UI's languages, Firefox
in all of them, each with gaps) and in English otherwise. The translations
are not in the repository: `ingest_browser_policies.py --translations`
fetches them at the same pinned revisions when the server is packaged.

At launch the node writes the policy as files under
`SEALSKIN_BROWSER_POLICY_FILES_PATH` (`/storage/sealskin_browser_policies`),
one folder per policy, and mounts each read-only where a browser reads it. A
change applies to the sessions started after it; a running session keeps the
policy it started with. An application whose policy is missing on the node
does not launch.

| Browser | Read at |
| --- | --- |
| Chromium, ungoogled-chromium, Helium | `/etc/chromium/policies/managed/sealskin.json` |
| Google Chrome | `/etc/opt/chrome/policies/managed/sealskin.json` |
| Brave | `/etc/brave/policies/managed/sealskin.json` |
| Microsoft Edge | `/etc/opt/edge/policies/managed/sealskin.json` |
| Opera | `/etc/opt/opera/policies/managed/sealskin.json` |
| Vivaldi | `/etc/vivaldi/policies/managed/sealskin.json` |
| Firefox | `/etc/firefox/policies/policies.json`, `/etc/firefox-esr/policies/policies.json` |
| LibreWolf | `/etc/librewolf/policies/policies.json`, `/usr/share/librewolf/distribution/policies.json` |
| Zen | `/etc/zen/policies/policies.json`, `/opt/zen/distribution/policies.json` |
| Mullvad Browser | `/app/Browser/distribution/policies.json` |

The Chromium paths are the ones each browser's binary names in the
linuxserver images, and defaults go to `recommended/` beside `managed/`. A
Chromium-family browser reads every file of its directory, so `sealskin.json`
adds to what an image ships. A Firefox-family browser reads one file, the
system one where it exists and the one of its install otherwise, so the
policy stands in place of an image's own `policies.json`: LibreWolf's and
Mullvad Browser's shipped defaults do not apply under a policy with a Firefox
part. A part left empty mounts nothing. Forks are best effort: a fork reads
the file, and may not honor every policy its engine defines.

The files are owned by root and mounted read-only, so the session's user
cannot change them. A user who is root in the container can still move a
policy directory aside or add a file to it, so a policy holds only where
sessions run without root: turn on desktop hardening in the user's or group's
settings, which takes `sudo` and the terminals away. A policy is configuration of the browsers an image holds, not
containment: a user who runs a browser of their own that reads another path
is not held by it.

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
  sysctls, ulimits, tmpfs, and extra groups; and on Kubernetes
  `KUBERNETES_POD_TEMPLATE`, a [PodTemplate](kubernetes.md#pod-templates-per-application)
  laid over the session pod. Only an administrator edits these.

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
directory. The base is an installed app or one the [Bakery](#bakery) baked,
whose baked image the meta-app then runs. Choose the base app, name the new
one, upload an icon, optionally paste an autostart script (the Wayland and X11 variants are separate), and
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

## Bakery

The Bakery bakes a new application on top of one you installed: packages
the image does not ship, files, certificates, fonts, anything a Dockerfile can
do, under a name and an icon of its own. Bake as many as you need from the
same application: three Chromiums with three sets of packages are three
recipes built on Chromium. Where the App Laboratory prepares a home
directory, the Bakery changes the image under it, and the two stack: the
laboratory offers every baked application as a base, and a meta-app made
from one runs its baked image with the home template the laboratory kept.
A session mounts its home directory at `/config`, over whatever the image
holds there, so a recipe puts its files anywhere else and the laboratory
keeps what belongs in the home.

**New Recipe** opens the bench. **Build on** lists the applications
installed from a store; an application the laboratory or the Bakery made is
not one to build on. Give the baked application its **Name** and, if you
like, an **Icon**. The image's distribution is read from the image itself
and shown beside its reference; **Distribution** overrides it for an image
that does not say. **Clear recipe** empties the steps, the Dockerfile, and
the uploaded files, and leaves the application, its name, and its icon.

In **Guided** mode the left pane is a list of steps and the right pane the
Dockerfile they render to, which follows every change; pointing at a step
lights its lines and clicking a line opens its step. The first line, `FROM`
the application's image, is not yours to change. The steps:

| Step | What it renders |
| --- | --- |
| Install packages | One `RUN` that installs with the image's package manager (`apk`, `pacman`, `apt-get`, `dnf`) and removes the caches the install left, the way the linuxserver images do. The search box lists the packages the image itself can install, and the quick picks add a named set: desktop companions, shell tools, build tools, archive tools. |
| Add an application | Chromium, Firefox, Google Chrome, Brave, or Visual Studio Code, from the distribution or, on Debian and Ubuntu, from its vendor's repository with the vendor's key. The Chromium-based ones start with `--no-sandbox`, a browser can be made the one that opens web links, and the application joins the right-click menu. An application a distribution cannot install is not offered on it. |
| Ubuntu PPA | `add-apt-repository` for the Launchpad archives you name, and the packages to install from them. Offered on Ubuntu images only. |
| Download | A file fetched while the image builds, checked against a SHA-256 if you give one: kept, unpacked as an archive, unpacked as an AppImage into a folder of its own, or installed as a package. |
| Development kit | The toolchain of Python, Node.js, Rust, Go, or Java from the distribution, and the language's own packages you list: pip packages in a virtual environment at `/opt/venv`, npm packages and built crates and Go modules under `/usr/local`. |
| Install Docker | Docker's static binaries in `/usr/local/bin` and the Compose and Buildx plugins in `/usr/local/lib/docker/cli-plugins`, the latest release at each build or the version you pin. The image's Docker service starts them in a privileged session. |
| Desktop entry | A `.desktop` file under `/usr/share/applications` for a program the image holds, with the name, command, icon, and menu category you give, and an item in the right-click menu. An AppImage you unpacked is started by its `AppRun`. |
| Launch command | The commands a session starts its application with, in place of the image's or before them (`/defaults/autostart` and `/defaults/autostart_wayland`). |
| Add fonts | Font sets the distribution packages (office-compatible, every weight of Chinese, Japanese, and Korean, Noto, the Microsoft core fonts under their license) and font files you upload. |
| Copy files | `COPY` of files you upload to a path in the image, made executable or unpacked from a tar archive if you ask. |
| Trust certificates | Your certificates in the distribution's trust store, and the command that takes them up. |
| Environment | `ENV` lines. |
| Proxy and mirrors | The proxy variables in both cases, with the loopback kept out of the proxy, and the index pip and the registry npm install from. They hold for the steps below and for every session. |
| Run at start | A script in `/custom-cont-init.d`, run as root each time the container starts, or a service in `/custom-services.d`. |

A session copies the launch command and the right-click menu into a home
directory on its first start there and keeps that copy, so a home directory
that already started keeps the ones it has.

Anything else, a command of your own or a Dockerfile instruction, is written
in developer mode.

A step can be left out of the build with the eye, without deleting it.

**Developer** mode is the Dockerfile itself, built exactly as written; it
starts from what the steps rendered. The application's image is the build
argument `BASE_IMAGE`: declare it with `ARG BASE_IMAGE` before the first
`FROM`, write build stages wherever you need them, and end on a stage that
starts `FROM ${BASE_IMAGE}`. The left pane holds the recipe's files, each
under `files/` in the build context, and the guided steps as snippets that
are rendered and put at the cursor. Under the text the page says what a session
would not survive (a last stage that is not the application's image, a
`USER` other than root, a replaced `ENTRYPOINT`, files under `/config`) and
what the node's builder does not read: builds use Docker's classic builder,
so BuildKit's `RUN --mount`, `COPY --chmod`, and here-documents are refused.
These are warnings, and the build is yours to run.

**Build a trial** builds the draft on the node the page is served from and
shows the builder's output as it comes. It changes nothing users run. When
it has built, **Open a trial session** launches the application on that
image with an empty home directory. **Save & bake** is the last step: it
keeps the recipe, adds the application, and returns to the list, where the
build's output runs under the table until it says the application is baked.
Every other node of a cluster builds it too when it sees the record. New
sessions run the new image once it has built; sessions already running keep
theirs. The terminal button of a row shows that recipe's build again, live
while one runs.

A baked image is rebuilt whenever its application's image is pulled again,
by the auto-update job or **Pull Latest**, and the rotate button in the list
bakes from scratch to take the distribution's current packages. When a build
fails, sessions keep running the last image that built and the list shows
**Failed** with the builder's output one click away; an application whose
recipe has never built on a node waits for the build at its first launch
there and fails with it.

A baked application and its recipe are one thing. Deleting it, here or
under **Installed Apps**, removes both. An application cannot be deleted
while something is built on it: an installed app while a baked application
is built on it, a baked application while a laboratory meta-app is built on
it. **Installed Apps** lists every application under the one it is built
on: a row that says **2 built on this** opens to show its baked
applications, and a baked one opens to show its laboratory meta-apps. The
row's **Delete** carries a lock and names what holds it; delete those
first. **Edit** on a baked application opens its recipe here. The
laboratory offers a baked application as a base only, never as a meta-app
to customize.

The recipes are `bakery/recipes.yml` and their uploaded files under
[`SEALSKIN_BAKERY_PATH`](settings.md), shared by the nodes of a cluster; the
images are each node's own, tagged `sealskin.local/bake/<application
id>:<key>`. In a cluster every node builds a recipe when it sees the record,
or at the first launch there when it has not pulled the application's image
yet, and the list shows the state of the image on each node under this
node's. A baked application's icon, like any uploaded icon, is shared by the
nodes. A build is given no registry credential: it starts from the
image the node already pulled. The Bakery needs the Docker provider; on
Kubernetes the tab says so and recipes cannot be saved.

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

Each GPU is shown by its model where the node can name it: the NVIDIA driver
names its own cards, and other GPUs are looked up in the PCI ID database
(`pci.ids`, from the `hwdata` package) when the server's image has one. A GPU
the node cannot name is shown by its render node and driver.

`SEALSKIN_NODE_GPUS` limits the GPUs offered to the devices it lists, and
`SEALSKIN_NODE_GPU_PREFER` names the kind a launch for any GPU gets first;
[Cluster](cluster.md#gpus) describes both.

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
