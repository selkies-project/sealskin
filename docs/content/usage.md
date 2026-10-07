---
title: Usage
description: The launcher, the right-click menu, downloads, sessions, storage, the file manager, collaboration rooms, and the web app.
---

Everything on this page happens in the client after it is
[connected](start.md#connect). The browser extension has the full set of entry
points; the mobile apps and the web app have the launcher, sessions, files,
and the dashboard but no context menu or download interception, because those
hooks only exist in a browser extension. The web app has its own
[entry points](#in-a-browser-with-nothing-installed) instead.

## The launcher

The toolbar icon opens the launcher. Its tabs:

* **Launch New** lists the applications you may use, filtered by what you are
  opening: only apps that declare URL support appear for a link, only apps
  registered for the file's extension appear for a file. A search box narrows
  long lists.
* **Active Sessions** shows your running sessions with **Re-open**, **Stop**,
  and **Send File** (drop a file into a running session's `Desktop/files`).
* **Manage Files** opens the [file manager](#the-file-manager).
* **Upload to Storage** appears when you arrived with a file and would rather
  keep it than open it: it goes straight into a home directory.
* **Upload Files** opens a page where you can drag a local file in and
  continue to the launcher with it.

Selecting an application reveals the launch options it supports:

| Option | Meaning |
| --- | --- |
| **GPU** | One of the GPUs the server detected, if your account may use them and the app supports that GPU type. |
| **Storage** | **Auto (per-app persistence)** mounts a home directory named `auto-<app>` that is created on first use. **Cleanroom (Ephemeral)** mounts a fresh directory that is deleted when the session stops. Any home directory you created is listed by name. Apps without home directory support, and accounts without persistent storage, always run in a cleanroom. |
| **Language** | The locale exported to the container (`LC_ALL`). The default is your browser's language. |
| **Collaborative Session** | Starts the app inside a [collaboration room](#collaboration-rooms) instead of a plain session. |
| **Wayland Mode** | Runs the container's Wayland compositor (the default) rather than the X11 session. Applications that misbehave under Wayland can be launched with it off. |
| **Open file on launch** | For files: whether the application should open the file immediately, or just receive it in `Desktop/files`. |
| **Save these launch options** | Remembers the application and options for this trigger: the toolbar button, "all URLs", or this file extension. The next time, the launcher skips straight to launching. Saved choices are listed under **Pinned Behavior** in the options page, where they can be removed. |

The client also sends your browser's time zone, so clocks inside the session
match yours.

**Launch** asks the server to start the container, waits until it answers
(up to a minute, longer on the first pull of an image), then opens the
session in a new tab. Sessions are streamed by Selkies: the tab is a full
desktop application with clipboard, audio, file transfer, gamepads, and the
rest, depending on what the [template](administration.md#app-templates)
enables.

## The right-click menu

The extension adds a **SealSkin** group to the context menu:

| Entry | Appears on | What it sends |
| --- | --- | --- |
| **Open Link in SealSkin** | links | The URL, to an app with URL support (a browser, typically). |
| **Open Link Target as File in SealSkin** | links | The extension downloads the target itself and offers it as a file, so the file never touches your disk. |
| **Send Media to SealSkin** | images, video, audio | The media file, fetched the same way. |
| **Search for "…" in SealSkin** | selected text | A search URL built from the search engine chosen on the connection page (Google by default). |
| **Send Next Download to SealSkin** | the page | Arms the interceptor (see below). Chrome only. |

Each of these opens the launcher with the context already filled in, or
launches immediately if you pinned a behaviour for it.

Files are uploaded to the server in chunks over the encrypted API. Large
files therefore take as long as your upstream bandwidth allows; the launcher
shows the progress.

## Intercepting downloads

**Send Next Download to SealSkin** puts a `…` badge on the toolbar icon and
watches for one minute. The next download the browser starts is cancelled
before it is written to disk, and the launcher opens with that file's URL so
the server fetches it into a session instead. This needs the Chrome
`downloads` API and is not available in Firefox or on mobile.

## Sessions

A session is one or more containers started for you, reachable through the
server's session proxy at `https://<server>:8443/<session id>/`. The launch
returns a one-time URL carrying an access token; opening it sets a cookie
scoped to that session path and every later request is checked by the proxy
against it. Nobody without the cookie reaches the container, and the
container itself is never exposed.

Sessions survive a server restart: they are recorded on disk and reattached
on start-up, and records whose containers are gone are discarded. Stopping a
session removes its containers and deletes any cleanroom storage. Sessions
have no idle timeout of their own; administrators can stop anyone's session
from the dashboard.

## Storage

Persistent data lives under `/storage/<username>/` on the server:

* **Home directories** are folders you (or an administrator) create. One is
  mounted as `/config` inside the container, which is where LinuxServer.io
  images keep the application's profile and settings. **Auto** storage uses a
  home directory named after the app.
* **Shared files** (`_sealskin_shared_files`) is one folder per user that is
  mounted at `/config/Desktop/files` in every persistent session, whichever
  home directory is in use. Files you upload, intercept, or send to a session
  land here, so every application sees the same files.
* **Cleanroom** sessions get throwaway versions of both, deleted when the
  session stops.

Administrators can disable persistent storage per user or group, in which
case every session is a cleanroom.

## The file manager

**Manage Files** in the launcher, or **Files** in the options page, opens a
file manager over your home directories and shared files. It can:

* browse and search, with paging for large directories,
* upload files and whole folders, by button or by dropping them on the page,
* create folders and delete files (deletion runs in the background and reports
  when it is done),
* download a file (in Chrome the extension streams it chunk by chunk through
  the encrypted API; on mobile it is saved to the app and opened with the
  system viewer),
* **open** a file in an application: the launcher opens with the server-side
  file as its context and the app gets it without another upload,
* **share** a file publicly, if your account allows it.

A public share copies the file into public storage and returns a URL of the
form `https://<server>:8443/public/<share id>` that anyone can open. A share
can carry a password (stored as a salted scrypt hash) and an expiry in hours;
expired shares are removed by a background job. **Public Shares** in the file
manager lists yours with their URLs and lets you revoke them.

## Collaboration rooms

Launching with **Collaborative Session** opens the app in a room at
`https://<server>:8443/room/<session id>` instead of a bare session. You are
the **controller**. The room page wraps the streamed application with:

* **Invite links** for two kinds of guest: **participants**, who can be given
  input, and **read-only viewers**. Guests need no SealSkin account; the link
  carries a token, and each guest receives a personal token on first visit.
* **Chat**, with display names guests choose for themselves.
* **Voice and video** between everyone in the room, with a designated speaker
  the controller can set.
* **Gamepad slots**: the controller drags the container's gamepad slots onto
  participants, and their local controllers are forwarded into the session. A
  participant given several slots plays one with each of their controllers, in
  the order the browser lists them: the first takes the lowest-numbered slot.
  Dragging a slot back to the gamepad box frees that slot alone.
* **Mouse and keyboard hand-over**: the controller can give one participant
  the mouse and keyboard, and take them back.
* **Application switching**: the controller can open another installed app
  inside the same room. Each app gets its own container that shares the
  session's storage, and the room switches between them; apps can be stopped
  or restarted individually.

Under the hood the room's WebSocket relays chat and control messages, and the
server pushes the current token table (who is controller, who holds which
slots, who has the mouse and keyboard) into every container of the session.
Stopping the session ends the room for everyone.

## On mobile

The iOS and Android apps host the same launcher, file manager, and dashboard.
Differences from the extension:

* No context menus and no download interception. Share a file into the
  launcher with **Upload Files** instead.
* Sessions open in the system browser (a Chrome Custom Tab or Safari view),
  not inside the app, because the streaming client needs a real browser
  engine.
* Downloads from the file manager are written to the app's storage and opened
  with whatever the system offers for that file type.
* A trusted TLS certificate is mandatory; the WebView refuses self-signed
  certificates and mixed content.

## In a browser, with nothing installed

`https://<server>:8443/` is the web app: the same launcher, file manager,
and dashboard in an ordinary tab. It has no configuration file and keeps no
key. [Sign in](signin.md) with what the administrator set up: **Sign In with
OpenID Connect** or **Sign In with SAML** takes you to your organization's
identity provider and back, a reverse proxy that signs you in opens the app
signed in, and the administrator of the server uses the **Root token**. The
dashboard shows who you are signed in as and has **Sign out**; a logout at
the identity provider ends the sign-in as well.

The web app fills the window. A rail on the left (a tab bar at the bottom of
a phone) moves between **Home**, **Sessions**, **Files**, and the dashboard,
and shows who is signed in. **Home** has a box that filters the applications
as you type and takes a pasted link to open in isolation, the sessions you
have running with **Open** and **Stop**, and the applications as tiles,
grouped by kind with your recent ones first. Click a tile for its launch
options, which are remembered per application, or its play button to launch
with them straight away. Drop a file anywhere on the page to open it in an
application that takes it.

Launching shows what the server is doing on the application's tile and in
the sessions row: choosing a node, preparing storage, downloading the
application the first time a node runs it, starting it, and waiting for its
desktop. The session then takes the web app's place in the tab; the
browser's back button is the way back. **Open in a new tab**, among the
launch options, opens a tab at the click instead, which shows the same
progress and becomes the session when it is ready, or says why it could not
start.

**Copy link**, beside **Launch**, copies the application's address with the
options chosen: `/app/<application id>/`, with the storage, the room, and the
rest in its query. Opening the address signs you in if need be and takes you
to your oldest running session of the application with that storage, room or
not, or launches one. Bookmark it, or install it from the browser's menu as
an app of its own, one per application; a session's own page offers the
same install, for the session's options.

Where the administrator turned session isolation on, a session has an
address of its own (`<session id>.<session domain>`) and opens in a tab of
its own. If the launcher then says the server has no such name the browser
reaches, the administrator has to give it wildcard DNS and a certificate for
it.

On a [cluster](cluster.md) the launcher has a **Where** choice: leave it on
**Automatic**, or pick a pool or a node. Each home directory shows the node
that holds it, and a session using it starts there. Where the administrator
allows, **Move storage** moves a home directory to another node once no
session is using it. A line under the launcher shows what is left of a time
allowance.

In place of the context menu:

* **The bookmarklets.** The dashboard's **SealSkin in This Browser** card has
  two links to drag to your bookmarks bar. **Send to SealSkin** opens the page
  you are on in SealSkin, or searches for the text you selected. **Pick for
  SealSkin** waits for your next click: click a link to open that link in
  SealSkin, or an image, video, or audio to send the file; Shift-click a link
  to send the file it leads to. A picked file is fetched by the page you are
  on, with your sign-in on that site, so it works for files only that site can
  read; anything the page cannot fetch opens as a link instead.
* **`web+sealskin:` links.** After **Open web+sealskin: Links Here** (Chrome,
  Edge, Firefox), a link such as `web+sealskin:https://example.com` opens
  its address in SealSkin.
* **Search.** The web app offers itself to the browser as a search engine
  (OpenSearch), and `…/?q=terms` and `…/?url=address` open the launcher
  with that search or address.
* **As an installed app** (Chrome and Edge, from the browser's install
  option): SealSkin becomes a target that other applications **share** links,
  text, and files to, and on a desktop it is offered to **open** the file types
  your installed applications open.

Sessions open in tabs of their own. While the web app's tab stays open,
**Re-open** brings a session's tab forward and **Stop** closes it; after the
web app is reloaded, and always in Safari, which lets the web app keep no
hold on the tab, **Re-open** opens the session in a new tab. Where the web app
cannot close the tab (Safari, and Chrome for a session at an address of its
own), the tab closes itself a few seconds after **Stop**, when its page
reconnects to the stopped session, or says the session stopped where the
browser does not let a page close its tab.

Each session opens at an address of its own (see
[Session origins](configuration.md#session-origins)), which keeps its pages
away from the web app and from other sessions altogether: session tabs get no
link back to the web app, it cannot be framed, and nothing a session's page
does can use your sign-in.

## The options page

The extension's options page (and the dashboard of the app and the web app)
is where the client configuration lives, alongside the account-level views:
**Configuration** (connection, export your config file for another device,
log out), **Home Directories**, **Active Sessions**, and **Pinned Behavior**.
Administrators see the management panels described in
[Administration](administration.md) in the same place.
