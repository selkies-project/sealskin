---
title: Usage
description: The launcher, the right-click menu, downloads, sessions, storage, the file manager and collaboration rooms.
---

Everything on this page happens in the client after it is
[connected](start.md#connect). The browser extension has the full set of entry
points; the mobile apps have the launcher, sessions, files and the dashboard
but no context menu or download interception, because those hooks only exist
in a browser.

## The launcher

The toolbar icon opens the launcher. Its tabs:

* **Launch New** lists the applications you may use, filtered by what you are
  opening: only apps that declare URL support appear for a link, only apps
  registered for the file's extension appear for a file. A search box narrows
  long lists.
* **Active Sessions** shows your running sessions with **Re-open**, **Stop**
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
desktop application with clipboard, audio, file transfer, gamepads and the
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
  home directory is in use. Files you upload, intercept or send to a session
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
* **Gamepad slots**: the controller assigns a participant to one of the
  container's gamepad slots and their local controller is forwarded into the
  session.
* **Mouse and keyboard hand-over**: the controller can give one participant
  the mouse and keyboard, and take them back.
* **Application switching**: the controller can open another installed app
  inside the same room. Each app gets its own container that shares the
  session's storage, and the room switches between them; apps can be stopped
  or restarted individually.

Under the hood the room's WebSocket relays chat and control messages, and the
server pushes the current token table (who is controller, who holds which
slot, who has the mouse and keyboard) into every container of the session.
Stopping the session ends the room for everyone.

## On mobile

The iOS and Android apps host the same launcher, file manager and dashboard.
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

## The options page

The extension's options page (and the app's dashboard) is where the client
configuration lives, alongside the account-level views: **Configuration**
(connection, export your config file for another device, log out), **Home
Directories**, **Active Sessions** and **Pinned Behavior**. Administrators see
the management panels described in [Administration](administration.md) in the
same place.
