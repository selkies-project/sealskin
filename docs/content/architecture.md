---
title: Architecture
description: The control and data planes, the encryption and authentication model, how a launch works, the served UI and the thin shells, and the persistence model.
---

## Components

A SealSkin installation is one server container and any number of clients.

```
 browser extension / mobile app                      server container (sealskin)
 ┌──────────────────────────────┐                   ┌─────────────────────────────────┐
 │ shell: connect page, host,   │  E2EE + JWT       │  Caddy  :8443  (data plane)     │
 │ background (keys, E2EE, JWT) │ ───────────────►  │   /ui/*, /api/*  ─► API         │
 │                              │                   │   /<session>/*   ─► forward_auth│
 │ iframe: UI served by server  │ ◄───────────────  │                     then proxy  │
 │  launcher, files, options    │  streamed session │   /room/*, /public/*            │
 └──────────────────────────────┘                   │                                 │
                                                    │  FastAPI :8000 (control plane)  │
                                                    │   handshake, launch, admin, ... │
                                                    │   Docker SDK ─► session         │
                                                    │                 containers      │
                                                    └─────────────────────────────────┘
```

* **The API server** (`server/app`, FastAPI on uvicorn) is the control plane:
  handshake, authentication, launching, sessions, files, shares, the
  collaboration WebSocket, and everything administrative. It listens on the
  API port (`8000`) over plain HTTP.
* **Caddy** is the data plane. It listens on the session port (`8443`) with
  the proxy certificate, forwards the API and the served UI to the API
  server, and reverse-proxies every session path to the right container
  after asking the API server whether the request may pass.
* **Session containers** are Selkies-based application images started
  through the Docker socket on the same network as the server, or as pods in
  the server's namespace on [Kubernetes](kubernetes.md). They are never
  published on a host port; only Caddy reaches them.
* **Clients** hold the user's private key and the server's public key,
  perform the handshake, sign tokens, and frame the web UI the server
  serves.

Both ports carry the same API. Caddy forwards `/api/*` on the session port
to the API server, so a client normally only needs `8443`; the API port is
published separately for the Chrome self-signed-certificate fallback, where
the extension talks plain HTTP to `8000` until the certificate has been
accepted. Even over plain HTTP the API payloads are encrypted end to end.

## Two lanes

The API is one set of routes that two kinds of client reach differently.

**The plain lane** carries the web sign-in and the calls nodes make to each
other. Requests and answers are JSON as it is, protected by TLS, and only on
a request that came through the node's own proxy: Caddy adds a secret to
everything it passes on, which a request made to the API port directly cannot
carry. The web app's requests authenticate by the cookie of a sign-in
(`sso.py`), a proxy's by the header a trusted address sets, a node's by a
signature of its server key. A cookie travels with any request the browser
makes to the origin, so each of these routes also asks the browser's own
account of where the request came from (`Sec-Fetch-Site`, else `Origin`) and
refuses anything but the web app's pages; and the page of a web sign-in's
session is the server's own copy of the application's web client
(`webclient.py`), never the container's, so no code of an image runs on the
origin that holds the cookie. With `session_isolation` such a session is
served only on an origin of its own as well.

**The encrypted lane** is the key-file clients', described next. A request
that names a crypto session takes it; a request to the API port always does.

## Encryption and authentication

**Handshake.** A client starts a crypto session with two unauthenticated
calls. `POST /api/handshake/initiate` returns a random nonce and its RSA-PSS
signature, which the client verifies with the stored server public key, so a
server that does not hold the private key is rejected before anything is
sent. The client then generates an AES-256-GCM key, wraps it with RSA-OAEP
under the server public key, and sends it to `POST /api/handshake/exchange`,
receiving a crypto session id. Session keys live in server memory and are
dropped after a day of inactivity (`SEALSKIN_CRYPTO_SESSION_TTL_SECONDS`).

**Encrypted routes.** Every other JSON endpoint is served by an
`EncryptedRoute`. Requests carry the crypto session id in `X-Session-ID` and
a body of the form `{"iv": ..., "ciphertext": ...}` (base64, AES-GCM). The
response body is wrapped the same way. A JSON response for a request without
a valid crypto session is refused rather than sent in the clear. Non-JSON
responses (the served UI, file downloads, redirects) pass through untouched.

**Idempotency.** A non-GET request with an `X-Idempotency-Key` header is
executed at most once per crypto session for ten minutes. A retry after a
network failure, for example while a container was being created, receives
the stored result; a retry that arrives while the first attempt is still
running waits for it.

**Users.** Authentication is a JWT (`RS256`) the client signs with its
private key and sends as `Authorization: Bearer`. The `sub` claim names the
user, `exp` is required, and one minute of clock skew is tolerated. The
server verifies the signature against the public key stored for that user
and refuses inactive accounts. There are no passwords and no server-side
user sessions to steal; the private key never leaves the client, and on the
extension it never leaves the background script.

**Sessions.** A launch returns a URL with a one-time `access_token`. Opening
`https://<server>:8443/<session id>/?access_token=...` hits the API server,
which sets an `HttpOnly`, `Secure` cookie scoped to `/<session id>` and
redirects to the clean URL. From then on Caddy's `forward_auth` sends every
request under that path to `/internal/resolve_session/<id>` on the loopback
API port; the handler checks the cookie (or, for rooms, a collaboration
token), and answers with the container's address and the HTTP basic-auth
credentials the container was started with, which Caddy injects upstream,
and, for a session whose web client the node exported, the directory of
that client: Caddy then serves the client's files itself, answers the
session's manifest and icon from the API, and sends the container only what
is under `/<session id>/api/`, with a content security policy that lets
nothing it answers run as a page. `/internal/*` is refused on the public
listener. Other origins get CORS
answers without `Access-Control-Allow-Credentials`, so no page elsewhere can
read what the cookie authenticates.

**Shares.** Public share passwords are stored as salted scrypt hashes. A
correct password yields a one-time download token, which stops working when
the share expires; the file itself is served from a directory that holds
copies, never from the user's home. Five wrong passwords within a minute
close a share's form with 429 until the oldest ages out, which bounds both
guessing and the scrypt work it costs the server.

## A launch, step by step

1. The client sends `POST /api/launch/simple`, `/url`, `/file`, or
   `/file_path` with the app, storage choice, language, time zone, GPU, and
   mode flags. For `/file`, the file was first uploaded in chunks to
   `/api/upload/*` under the user's own upload directory.
2. The server resolves the installed app (store entry merged with
   overrides), checks the user may see it, and decides the storage: a named
   home directory, the `auto-<app>` directory, a meta-app template copy, or
   a fresh ephemeral directory. Every persistent session also gets the user's
   shared files directory mounted at `Desktop/files`.
3. It builds the launch spec: the session environment (`SUBFOLDER`, `PUID`,
   `PGID`, `TZ`, the per-session basic-auth credentials, `PIXELFLUX_WAYLAND`,
   `LC_ALL`), the template's variables, the request's `SEALSKIN_URL` or
   `SEALSKIN_FILE`, the app's environment overrides, the GPU device, the
   volumes, and Docker run options merged from the store entry and the
   template's `DOCKER_*` settings. If the app has an autostart script, it is
   written into the home directory as the `openbox` or `labwc` autostart.
4. The provider starts the instance: the Docker provider pulls the image if
   needed and runs the container (removed on exit) on the server's network;
   the Kubernetes provider creates a pod and waits for it to be scheduled and
   pull its image. Either then polls it over HTTP with the session
   credentials until it answers, up to a minute.
5. The session is recorded in `sessions.yml` and the one-time URL returned.
   Rooms get a controller token and invite tokens as well, and the container
   is told the initial token table.

Stopping reverses it: containers are stopped, ephemeral directories deleted,
the record removed, and room participants notified. The server does the same
for a session whose container ended on its own, which it checks every 30
seconds, and removes containers it labelled that no session references.

## Served UI and thin shells

Since 0.3.0 the launcher, options and admin dashboard, file manager, upload
page, and collaboration room are one web application built from `client/` and
served by the API server under `/ui/`, the web app's own page at `/`. The browser extension and the mobile
app bundle only what cannot be served:

* the **connection page**, the one page that works with no server,
* the **background script**: context menus, download interception, the
  handshake and encrypted fetch, JWT signing, the session-to-tab map, and
  Chrome's streaming download handler,
* the **host page**, which frames the served UI and relays its requests to
  the background.

The [web app](#web-app) is a third shell that the server serves itself, so it
needs no store at all.

Updating the server image therefore updates the UI everywhere; a store
release is needed only when the manifest, the native plugins, or the bridge
protocol change. HTML entry points are served with `Cache-Control: no-cache`
and every other asset has a content hash in its name and is cached for a
year, so browsers pick up a new UI on the next load.

### Host states

The host page owns one iframe and cycles through three states:

1. **No configuration**: the bundled connection page is framed.
2. **Configured**: the host asks the background for the served UI base
   (`https://<server>:<session port>` first, then the `http://<server>:<api
   port>` fallback), points the iframe at `<base>/ui/<page>.html` and waits
   for the page's `hello`.
3. **No `hello` within eight seconds**, or the manifest fetch failed: the
   unreachable panel, with **Retry**, **Open server** (opens the web app at `<base>/` in
   a top-level tab so a self-signed certificate can be accepted), and
   **Change connection**.

If the page's bridge version differs from the shell's, the host shows an
**Update required** panel instead of a broken UI.

### The bridge

Served pages never call `chrome.*`. They send `postMessage` requests to the
host, `{sealskin: 1, id, type, payload}`, and receive `{sealskin: 1, id, ok,
data}` or an error. The host accepts only messages from its own iframe whose
origin matches the base it loaded; the page accepts only replies from its
parent, and only when that parent is a shell: an extension page, the mobile
app's `capacitor://localhost` (iOS) or `https://localhost` (Android), or the web
app on the page's own origin. Structured
clone carries `File` and `Blob` objects. The private key
never crosses the bridge: signing and encryption stay in the background.

| Request | Payload | Reply | Notes |
| --- | --- | --- | --- |
| `hello` | `{bridge, uiVersion}` | shell, platform, shell version, locale, capabilities, connection config | The first message; the host treats it as the ready signal. |
| `secureFetch` | `{url, options}` | decrypted JSON | The host adds the JWT, encrypts the body, and decrypts the response. |
| `getContext` | | pending launch context or `null` | Clears it. `{action: 'url' \| 'file' \| 'search' \| 'server-file', targetUrl?, filename?, selectionText?, file?}`. |
| `setContext` | `{context, openPopup}` | | Used by the files and upload pages. |
| `fetchBlob` | `{url}` | `Blob` | Fetched with extension privileges; link targets and media. |
| `openSession` / `focusSession` / `closeSession` | session | | Open, focus, or close the tracked tab; close also deletes the session. |
| `reserveTab` | `{reserve}` | | Web app: opens a blank tab during the **Launch** click for `openSession` to fill, or closes it when the launch fails. |
| `openPage` | `{page, params?}` | | `popup`, `options`, `files`, `upload`, `connect`. |
| `openExternal` | `{url}` | | New tab or Custom Tab. |
| `downloadFile` | `{home, path, filename}` | | Chrome only (`capabilities.streamDownload`): the service worker streams the file. |
| `saveBlob` | `{blob, filename}` | | Mobile only (`capabilities.nativeFileOpen`). |
| `storageGet` / `storageSet` / `storageRemove` | keys | | Limited to the launch profiles and the pending connection config. |
| `updateConfig` | `{searchEngineUrl?, userSettings?}` | | Only these fields; keys and the server address belong to the connection page. |
| `close` | | | Closes the popup; a no-op on mobile. |
| `saveConfig` / `getConnectConfig` / `clearConfig` | | | Honoured only while the bundled connection page is framed. |

`File` contexts are parked in IndexedDB inside the shell origin so that
Chrome's JSON-only service worker messaging never has to carry them.

### Mobile

The Capacitor app's `index.html` is the host. It installs a small `chrome.*`
polyfill (`chrome.storage.local` over `localStorage`, `chrome.tabs.create`
over the Browser plugin, `chrome.action.openPopup` setting the iframe) and
runs the same background script as the extension in the outer window.
Native file opening and the back button stay outside the iframe. The WebView
requires a trusted certificate.

### Web app

The web app at `/` (`client/src/ui/index.html` and `app.js`) is a host: like the
mobile app it installs the `chrome.*` polyfill and runs the background script
in the page, and frames the served pages. It talks only to the server that
serves it, and only on the plain lane: its `secureFetch` is a same-origin
`fetch` that carries the sign-in's cookie, with no handshake, key, or token.
It draws the sign-in panel itself (the provider buttons `/api/auth/config`
offers and the root token field), exchanges the grant a finished provider
flow leaves in the URL fragment, and shows the panel again whenever a call
answers 401. Where a reverse proxy signed the user in, there is no panel:
the web app reloads when the proxy's sign-in runs out, so the proxy sends
the browser to its own page, and `/#root` asks for the root token
regardless.

The host draws the frame around the pages: the rail of destinations and the
signed-in user. Its launcher is `home.html`, a full page, where the extension
and the mobile app frame `popup.html`.

A launch names itself (`launch_id`) and reserves its tab at the click on
`launching.html`, a page of the web app's own origin that asks
`/api/launch/progress/<id>` what stage the launch is at (`progress.py`),
shows it, and navigates itself to the session when it is ready. The tab is
never navigated by the page that opened it, so nothing depends on keeping a
handle to it.

A session opens at `/<id>/` on the web app's origin (see
[Session origins](configuration.md#session-origins)). With session
isolation it opens on an origin of its own instead: the shells probe a
random `<id>.<session domain>`, `<id>.<server name>`, and `<id>.<parent
name>`, and open the session under the first that answers, and the server
refuses the session anywhere else (`on_session_origin`). Caddy refuses other
origins' requests and WebSockets on a session path, so sessions on sibling
origins, which are same-site, cannot reach one another, and the API refuses
a cookie's request that the browser does not mark as the web app's own.

* **Isolation.** The page is sent with `Cross-Origin-Opener-Policy:
  same-origin-allow-popups` and `frame-ancestors 'none'`, so a session page
  that opens or frames it gets no handle on it. The page opens every session
  tab itself (a `reserveTab` at the click, since a tab opened when a long
  launch ends is blocked as a popup), clears its `opener`, and keeps the
  handle to focus and close it; it never looks a tab up by name, which would
  make it the tab's opener. WebKit severs every tab a page with that COOP
  opens, and Chrome lets no page close a cross-origin tab it gave no opener,
  so a stopped session's tab closes itself instead: its page reloads to
  reconnect, and Caddy's check answers a page load of a session that is not
  running with a page that closes its tab (`internal.stopped_page`). Caddy
  drops `Service-Worker-Allowed` from session responses, so a session's
  service worker cannot claim `/ui/`, and the web app's, registered with the scope `/ui/`, never claims a session.

`/ui/manifest.webmanifest` makes it installable. Its share target posts to
`/ui/share`, where the service worker (`sw.js`, which caches nothing) parks
the link, text, or file for the launcher; its file handlers list the
extensions of the installed applications that have a MIME type, since Linux
desktops register the pair; and it handles `web+sealskin:` links.
`/ui/opensearch.xml` offers the app as a search engine. `?url=` and `?q=` open
the launcher with a link or a search, which is also how the dashboard's
bookmarklet sends a page or a selection.

## Clusters

[Clusters](cluster.md) describes what an administrator sees. Underneath:

* **Shared records** are objects under fixed keys (`store.MOUNTS`) in an
  `ObjectStore`: a node's own files, a bucket, or another node's files over
  the peer listener. `persistence` routes a path under a mount to the store,
  writes it on the condition that it is as this node last read it, and
  reloads what another node changed.
* **Peer calls** go to a node's peer listener, a second Caddy site that
  serves a certificate the node issued itself. A caller trusts the
  certificates in the approved nodes' records and signs each request with its
  server key, binding method, path, and body; a frontend's signature may
  carry the user the call acts for.
* **Forwarding.** A frontend answers what it can and sends the rest on
  unchanged: the dependencies in `routing` find the node a session or home
  directory is on, or the node placement picks for a launch, and return that
  node's answer.
* **Session traffic.** Caddy asks the API where a session path goes
  (`/internal/resolve_session`, `/internal/route`). For a session on another
  node the answer is that node's peer listener, and the node authorizes the
  request itself with the session's cookie, so a session's credentials stay
  where it runs.
* **Live state** is not shared: every node asks every other for its sessions
  and load on a timer, and a node rebuilds its own sessions from its backend.

## Persistence

All state is YAML and flat files under `/config`, described in
[Configuration](configuration.md). The `persistence` module is the single
reader and writer: atomic replacement, one lock per path so concurrent
handlers never interleave, a hash of the last write so the file watcher can
tell the server's own writes from an administrator's edits, and a
`watchfiles` loop that reloads the affected file on change. The files the
nodes of a cluster share go through the object store the same way, wherever
it keeps them.

Installed apps are stored as references plus overrides and resolved against
the cached store entry on every load, so the file stays small and store
updates apply without reinstalling. Sessions are saved at launch and
reconciled with the backend at start-up and every 30 seconds after.

## Server modules

```
server/main.py                thin wrapper: from app.main import main; main()
server/setup.py               PEP 517 hook that reads ../VERSION for the wheel
server/app/main.py            renders the Caddyfile, starts Caddy and uvicorn
server/app/api.py             FastAPI factory, lifespan, background jobs, file watcher
server/app/settings.py        setting definitions (environment variables), UI path resolution
server/app/version.py         VERSION from the package (wheel) or the repo root (source)
server/app/state.py           one container for all in-memory state
server/app/persistence.py     atomic YAML read/write, per-file locks, change watcher, store routing
server/app/store.py           the object store: files, S3, another node's files, and the local mirror
server/app/cluster.py         node identity, registry, peer calls, live state, placement, joining
server/app/routing.py         dependencies that forward a request to the node it concerns
server/app/quota.py           session, storage, and time limits; usage records
server/app/progress.py        the stage a launch is at, for the page that waits on it
server/app/homes.py           moving a home directory between nodes
server/app/filesync.py        the users' shared files kept in the store
server/app/sso.py             web sign-ins: OpenID Connect, SAML, the root token
server/app/config_store.py    stores, installed records and resolution, templates, sessions, shares
server/app/security.py        handshake, EncryptedRoute, the lanes and who a request is, password hashing
server/app/launch.py          build_launch_spec(), launch_application(), stop and swap
server/app/docker_utils.py    Docker client and self-inspection, render nodes, image cache
server/app/fsutil.py          filesystem helpers
server/app/user_manager.py    users, admins and groups; how the settings of several groups combine
server/app/providers/         provider interface, the Docker and Kubernetes providers
server/app/routers/           one router per area: handshake, applications, launch, sessions,
                              homedirs, admin, uploads, files, shares, internal, ui
server/app/collaboration.py   room page, WebSocket and token fan-out
server/app/Caddyfile.tpl      Caddy configuration template
server/app/template_schema.yml  template editor variable definitions
server/app/static/            landing page and the share password page
server/tests/                 pytest suite
```

The [Server Reference](reference/index.mdx) is generated from these modules'
docstrings.

## Client layout

```
client/build.mjs              esbuild pipeline; writes client/dist/{ui,extension,mobile}
client/src/ui/                served pages: popup, options, files, upload, css/, room/; the web app (index, app, sw)
client/src/shell/             host, connect page, background script, mobile host
client/src/lib/               bridge, host-bridge, api, crypto-utils, i18n, dom, languages
client/src/i18n/<lang>.json   one file per language, merged over English at build time
client/vendor/                vendored Font Awesome; no CDN
browser_extension/            manifests, icons, build.sh (zips client/dist/extension)
mobile/                       Capacitor project; www/ is copied from client/dist/mobile
```

[Development](development.md) covers the build.
