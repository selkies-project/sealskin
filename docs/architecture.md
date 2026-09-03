# SealSkin 0.3 architecture

This document describes the layout introduced in 0.3.0: one built web UI served
by the server, thin store-published shells (browser extension, mobile app), a
fixed bridge contract between the two, and the YAML persistence model.

## 1. Repository layout

```
VERSION                      single source of truth for every version number
client/                      all web UI source and the build
  build.mjs                  esbuild-based build, writes client/dist/
  package.json
  src/
    ui/                      pages served by the server (/ui/)
      popup.html popup.js
      options.html options.js
      files.html files.js
      upload.html upload.js
      css/ ...
    shell/                   pages bundled into the store packages
      connect.html connect.js   login / connection page (works with no server)
      host.html host.js         frame host: picks connect page or served page
      background.js             extension background (context menus, downloads,
                                E2EE session, JWT signing, bridge relay)
    lib/                     shared modules imported by both
      bridge.js              page side of the bridge (window.parent postMessage)
      crypto-utils.js
      i18n.js                loads i18n/<lang>.json, exposes t()
      dom.js                 applyTranslations, formatBytes, timeAgo, escapeHtml
      api.js                 secureFetch wrapper on top of bridge
    i18n/<lang>.json         one file per language (split from translations.js)
    languages.js             locale list for the launch language dropdown
  vendor/fontawesome/        vendored icon font, no CDN
  dist/                      build output (git-ignored)
    ui/                      served by the server  -> server static mount
    extension/               contents of the extension zip (minus manifest)
    mobile/                  contents of the mobile web dir
browser_extension/           manifests, icons, build.sh (zips client/dist/extension)
mobile/                      Capacitor project; www/ is copied from client/dist/mobile
server/                      Python API + Caddy
  main.py                    thin wrapper (from app.main import main; main())
  setup.py                   PEP 517 build hook (reads ../VERSION)
  app/ ...                   see section 6
  app/template_schema.yml    env var definitions for the template editor
```

## 2. Versioning

`VERSION` holds e.g. `0.3.0`. Consumers:

* `client/build.mjs` reads it and stamps `dist/ui/manifest.json`, the extension
  manifests it copies to `dist/extension/manifest.*.json`, and mobile
  `package.json` version.
* `browser_extension/build.sh` and `mobile/build.sh` read it for artifact names
  and the Android versionCode (`major*10000 + minor*100 + patch`).
* `server/app/version.py` reads it for the API and `/ui/manifest.json`.
* `release-notes/<VERSION>.md` must exist for a stable release.

## 3. Build

`cd client && npm install && npm run build` produces `client/dist/`.

* Each page in `src/ui` is an esbuild entry. JS and CSS get content-hashed
  names (`popup.<hash>.js`); the HTML files are rewritten to reference them.
  HTML entry points are served with `Cache-Control: no-cache`, hashed assets
  with `immutable`.
* `src/i18n/*.json` are emitted as hashed JSON. Each language file is merged
  over English at build time so the runtime needs one fetch.
* `template_schema` is NOT built; the server serves it from
  `server/app/template_schema.yml`.
* `dist/extension/` receives: `background.js` (bundled, unhashed, the manifest
  names it), `host.html`, `host.js`, `popup.html` and `options.html` (tiny host
  entry pages), `connect.html`, `connect.js`, `connect.css`, the connect page
  i18n subset (prefixes listed in `src/shell/i18n-namespaces.json`, including
  the `shell.host.*` strings for the host panel), icons. No served page is
  bundled into the extension.
* `dist/mobile/` receives: `index.html` (host), `host.js` bundle including the
  mobile polyfill and native plugins, `background.js`, connect page assets,
  icons.
* `dist/ui/` receives the served pages plus `manifest.json`:
  `{"version": "0.3.0", "bridge": 1}`.
* The collaboration room (`server/app/static/collaboration`) moves under
  `client/src/ui/room/` and is emitted to `dist/ui/room/` with hashing. The
  server route `/room/{session_id}` renders `dist/ui/room/room.html` with the
  iframe src substituted as today.

## 4. Shells and the served UI

### 4.1 Host states

`host.js` runs inside an extension page (or the mobile outer window) and owns
one iframe.

1. **No config** (no server / username / private key stored): load the bundled
   `connect.html` in the iframe.
2. **Config present**: ask the background for the served UI base (the E2EE
   session base URL, which is `https://host:sessionPort` or the
   `http://host:apiPort` fallback). Set the iframe to
   `<base>/ui/<page>.html`. Wait for the page's `hello` bridge message.
3. **No `hello` within 8 seconds**, or the manifest fetch failed: show the
   unreachable panel: server address, a retry button, an "Open server" button
   (opens `<base>/ui/` in a top-level tab so a self-signed certificate can be
   accepted) and a "Change connection" button (loads connect page).

Candidate bases are tried in order: https session port first, then http API
port. This keeps the Chrome self-signed workflow: if https fails before the
certificate has been accepted, the http origin is tried; once the user has
accepted the certificate in a top-level tab, https works for both the API and
the iframe.

### 4.2 Bridge contract (version 1)

Transport: `window.postMessage`. Page to host requests are
`{sealskin: 1, id, type, payload}`; host to page replies are
`{sealskin: 1, id, ok, data}` or `{sealskin: 1, id, ok: false, error}`. The
host only accepts messages whose `event.source` is its iframe's window and
whose `event.origin` matches the base it loaded. The page only accepts replies
from `window.parent`. Structured clone carries `Blob`/`File` objects.

| type | payload | reply | notes |
|---|---|---|---|
| `hello` | `{bridge: 1, uiVersion}` | `HelloInfo` (below) | first message; host uses it as the ready signal |
| `secureFetch` | `{url, options}` | decrypted JSON | host adds JWT for `/api/admin`, `/api/homedirs`, `/api/sessions`, `/api/files`, encrypts body, decrypts response |
| `getContext` | – | pending launch context or `null` | clears it. Context: `{action: 'url'|'file'|'search'|'server-file', targetUrl?, filename?, selectionText?, file?: File}` |
| `setContext` | `{context, openPopup: bool}` | `{}` | used by files (server-file) and upload (File object) |
| `fetchBlob` | `{url}` | `Blob` | host fetches with extension privileges (`<all_urls>`); used for link targets and media |
| `openSession` | `{sessionId, sessionUrl}` | `{}` | opens and tracks a session tab (mobile: Custom Tab) |
| `focusSession` | `{session}` | `{}` | focuses the tracked tab or opens a new one |
| `closeSession` | `{sessionId}` | `{}` | closes the tab and DELETEs the session |
| `openPage` | `{page, params?}` | `{}` | `popup`, `options`, `files`, `upload`, `connect`. Optional `params` become the framed page's query string (files uses `home`). Extension popup: opens `host.html?page=<page>` in a tab and the page then calls `close`; in a tab or on mobile the iframe navigates in place |
| `openExternal` | `{url}` | `{}` | new tab / Custom Tab |
| `downloadFile` | `{home, path, filename}` | `{}` | only when `capabilities.streamDownload` (Chrome); host triggers the service worker stream |
| `saveBlob` | `{blob, filename}` | `{}` | only when `capabilities.nativeFileOpen` (mobile) |
| `storageGet` | `{keys}` (array or null) | object | limited to `simple_launch_profile`, `workflow_profile_*`, `sealskinPendingConfig` |
| `storageSet` | `{items}` | `{}` | same key rules |
| `storageRemove` | `{keys}` | `{}` | same key rules |
| `updateConfig` | `{searchEngineUrl?, userSettings?}` | `{}` | only these fields; keys and server address are owned by the connect page |
| `close` | – | – | closes the popup (extension); mobile: no-op |
| `saveConfig` / `getConnectConfig` / `clearConfig` | connect page only | – | the host refuses these unless the bundled connect page is framed |

`HelloInfo`:

```
{
  bridge: 1,
  shell: 'extension' | 'mobile',
  platform: 'chrome' | 'firefox' | 'android' | 'ios' | 'web',
  shellVersion: '0.3.0',
  locale: 'en-US',
  capabilities: { streamDownload, nativeFileOpen, contextMenus, tabs },
  config: { serverIp, apiPort, sessionPort, username, searchEngineUrl, userSettings }
}
```

The private key never crosses the bridge. JWT signing and E2EE stay in the
background script, which now signs a JWT for every `/api/` call except the
handshake. `File` contexts are parked in IndexedDB in the shell origin so the
Chrome service worker (JSON-only messaging) never sees them.

### 4.3 Served pages

Pages import `lib/bridge.js` and call `bridge.hello()` at startup. They must not
reference `chrome.*` or `window.parent.*` directly. Mobile-specific layout is
applied when `hello.shell === 'mobile'` by adding `shell-mobile` to `<html>`;
`mobile-overrides.css` lives in the client build and is scoped to that class.

### 4.4 Connect page

Bundled only. Fields: server address, API port, session port, username, client
private key (paste, generate, or import config JSON), server public key, search
engine. Actions: test connection (handshake + `/api/admin/status` through the
background), save, export config. On success it messages the host (`openPage`
`popup`).

### 4.5 Mobile

`mobile/index.html` is the host; it loads `background.js` and `host.js` in the
outer window. The polyfill shrinks to what `background.js` needs there
(`chrome.storage.local` on localStorage, `chrome.tabs.create` via the Browser
plugin, `chrome.action.openPopup` sets the iframe). Native file open and the
back button stay in the outer window. Self-signed certificates are not
supported in the mobile WebView; a valid certificate is required.

## 5. Persistence (YAML, hand-editable)

All files stay YAML under `/config/.config/sealskin/`. Changes:

* `app/persistence.py`: `read_yaml(path)`, `write_yaml(path, data)` (temp
  file + `os.replace`), an `asyncio.Lock` per path, and a watcher that reloads
  a file when it changes on disk (uses `watchfiles`).
* `installed_apps.yml` stores **references plus overrides**:

  ```yaml
  - id: <uuid>
    source: <store name>
    source_app_id: <store app id>
    app_template: Default
    users: []
    groups: []
    auto_update: true
    overrides:            # only fields the admin changed, merged over the store entry
      name: My Firefox
      provider_config:
        env: [{name: FOO, value: bar}]
  ```

  Meta-apps additionally carry `is_meta_app`, `base_app_id`,
  `home_template_name`, and their own `name`/`logo`/autostart scripts under
  `overrides`. Effective apps are computed by `config_store.resolve_app()` as
  store entry deep-merged with overrides. Legacy full-snapshot records are
  migrated on load: fields equal to the store entry are dropped, the rest become
  overrides, and the file is rewritten once.
* Partial update: `PATCH /api/admin/apps/installed/{id}` accepts a partial body;
  record fields (`users`, `groups`, `app_template`, `auto_update`) apply directly,
  everything else merges into `overrides`. `PUT` still accepts the full shape.
* Uploads live under `<upload_dir>/<username>/<uuid>`; the upload id must be a
  lowercase UUID and every consumer checks ownership.
* Sessions are saved on launch. Invalid records are skipped with a log line,
  never wipe the list.
* Template file name is derived once and stored alongside the template in
  memory; renaming the `name` key renames the file.

## 6. Server modules

```
server/main.py                    thin wrapper: ``from app.main import main; main()``
server/setup.py                   PEP 517 build hook (reads ../VERSION for the wheel)
server/app/__main__.py            enables ``python -m app``
server/app/main.py                entry: Caddy + uvicorn
server/app/version.py             reads VERSION from package dir (wheel) or repo root (source)
server/app/settings.py            settings definitions; ui_path resolves from package (wheel) or repo (source)
server/app/state.py               runtime state container (apps, stores, templates, sessions, crypto sessions, locks)
server/app/persistence.py         atomic YAML + locks + watcher
server/app/config_store.py        load/save/resolve apps, stores, templates, store cache, autostart cache
server/app/security.py            E2EE handshake, EncryptedRoute, request decryption, JWT verification deps, scrypt share passwords
server/app/fsutil.py              copytree/rmtree/unique filename/sanitize helpers
server/app/launch.py              build_launch_spec(): env, volumes, GPU, autostart, collab tokens; used by launch routes and collaboration
server/app/docker_utils.py        self-inspection, host path translation, GPU detection
server/app/providers/             unchanged interface; all Docker access goes through DockerProvider
server/app/routers/handshake.py
server/app/routers/applications.py
server/app/routers/launch.py
server/app/routers/sessions.py
server/app/routers/homedirs.py
server/app/routers/admin.py
server/app/routers/uploads.py
server/app/routers/files.py
server/app/routers/shares.py
server/app/routers/internal.py    Caddy forward_auth only (resolve_session; the unauthenticated resolve_upstream route was removed)
server/app/routers/ui.py          /ui static mount, /ui/manifest.json, /api/ui/template_schema, / landing
server/app/collaboration.py       websocket + room page
server/app/api.py                 app factory, lifespan, router registration
```

All modules use Google-style docstrings and type hints. Caddy denies
`/internal/*` before the catch-all.
