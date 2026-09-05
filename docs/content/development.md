---
title: Development
description: Running the server from source, building the client, loading the extension, the mobile shells, tests, conventions and this documentation site.
---

## Repository layout

```
VERSION              the one version number: server, UI, extension, app
server/              Python API server, Caddy template, tests, wheel packaging
client/              web UI source and the esbuild pipeline (dist/ui, dist/extension, dist/mobile)
browser_extension/   manifests, icons and the zip script for the extension shell
mobile/              Capacitor project for the iOS and Android shells
docs/                this site (Fumadocs) with the pages under docs/content
release-notes/       one Markdown file per stable release
.github/workflows/   CI, Pre-release, Release, Mobile and Docs
```

The server is a FastAPI application; the client is plain JavaScript bundled
by esbuild with no framework; the shells are the same JavaScript packaged for
Chrome, Firefox and Capacitor.

## Prerequisites

* Python 3.11 or newer (3.13 in CI)
* Node.js 22 or newer
* Caddy on the `PATH` (the server starts it as a child process)
* Docker, with your user able to reach `/var/run/docker.sock`
* For the mobile shells: Android Studio with a JDK 21, or Xcode

## The server

```bash
cd server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt ruff pytest pytest-asyncio
ruff check .
pytest
```

The tests redirect every path setting into a temporary directory and never
touch Docker, so they run anywhere.

### Running it locally

The defaults assume the container's `/config` and `/storage`. To run on a
workstation, point the paths at a scratch directory and supply the two key
files the server refuses to start without:

```bash
mkdir -p ~/sealskin-dev/config/ssl ~/sealskin-dev/storage
cd ~/sealskin-dev/config/ssl
openssl genpkey -algorithm RSA -out server_key.pem -pkeyopt rsa_keygen_bits:4096
openssl req -new -x509 -days 365 -nodes -out proxy_cert.pem -keyout proxy_key.pem -subj "/CN=localhost"
```

Then, from `server/` with the venv active:

```bash
export SEALSKIN_SERVER_PRIVATE_KEY_PATH=~/sealskin-dev/config/ssl/server_key.pem
export SEALSKIN_PROXY_CERT_PATH=~/sealskin-dev/config/ssl/proxy_cert.pem
export SEALSKIN_PROXY_KEY_PATH=~/sealskin-dev/config/ssl/proxy_key.pem
export SEALSKIN_CADDYFILE_PATH=~/sealskin-dev/config/Caddyfile
export SEALSKIN_INSTALLED_APPS_PATH=~/sealskin-dev/config/installed_apps.yml
export SEALSKIN_APP_STORES_PATH=~/sealskin-dev/config/app_stores.yml
export SEALSKIN_APP_TEMPLATES_PATH=~/sealskin-dev/config/app_templates
export SEALSKIN_KEYS_BASE_PATH=~/sealskin-dev/config/keys
export SEALSKIN_GROUPS_BASE_PATH=~/sealskin-dev/config/groups
export SEALSKIN_SESSIONS_DB_PATH=~/sealskin-dev/config/sessions.yml
export SEALSKIN_PUBLIC_SHARES_METADATA_PATH=~/sealskin-dev/config/public_shares.yml
export SEALSKIN_AUTOSTART_CACHE_PATH=~/sealskin-dev/config/autostart_cache
export SEALSKIN_APP_STORE_CACHE_PATH=~/sealskin-dev/config/app_stores_cache
export SEALSKIN_STORAGE_PATH=~/sealskin-dev/storage
export SEALSKIN_UPLOAD_DIR=~/sealskin-dev/storage/uploads
export SEALSKIN_PUBLIC_STORAGE_PATH=~/sealskin-dev/storage/public
export SEALSKIN_HOME_TEMPLATES_PATH=~/sealskin-dev/storage/home_templates
export SEALSKIN_APP_ICONS_PATH=~/sealskin-dev/storage/app_icons
export HOST_URL=localhost
python main.py          # or: python -m app
```

The same works from the release wheel: `pip install
sealskin_server-<VERSION>-py3-none-any.whl` installs the `sealskin-server`
command with the built UI, the Caddyfile template and all dependencies
inside the package, so only the environment above and the key files are
needed.

The first start creates the `admin` account and writes `admin.json` into
`~/sealskin-dev/config`, three levels above the keys directory. Build the
client first (below) or the server has no UI to serve; `SEALSKIN_UI_PATH`
overrides where it looks. The [Settings Reference](settings.md) lists every
variable.

Outside a container the server cannot inspect itself, so host path
translation is the identity and the discovered ports are the configured
ones. Sessions are still started through Docker on the local daemon, and
the container has to be able to reach the session's IP on the default bridge
network, which is the case on a Linux host.

### Conventions

* Google-style docstrings on every module, class and public function, with
  type hints on the signature. Ruff enforces the `D` rules with the Google
  convention; see `pyproject.toml`. The
  [Server Reference](reference/index.mdx) is rendered from these docstrings
  as Markdown, so inline code goes in single backticks and cross-references
  are plain `` `name` `` rather than Sphinx roles.
* Settings are declared once in `SETTING_DEFINITIONS`; a new setting means
  an entry there and a regenerated [Settings Reference](settings.md)
  (`npm run generate:settings` in `docs/`).
* All Docker access goes through `docker_utils` and the provider; all YAML
  goes through `persistence`; all launches go through `build_launch_spec`.

## The client

```bash
cd client
npm install
npm run build                         # dist/ui, dist/extension, dist/mobile
npm run build -- --target ui          # one target
npm run watch                         # rebuild on change under src/
SEALSKIN_BUILD_STRICT=1 npm run build # fail if any page or entry is missing
```

| Output | Consumer | Hashed | Minified |
| --- | --- | --- | --- |
| `dist/ui` | served by the server under `/ui/` | yes | yes |
| `dist/extension` | contents of the extension zip | no | no (store review friendly) |
| `dist/mobile` | Capacitor web directory (`mobile/www`) | no | no |

The build reads every HTML page of a target and turns each local
`<script src>` and `<link rel="stylesheet" href>` into an esbuild entry,
then rewrites the tag to the emitted file name:

* `<script type="module">` is bundled as ESM; a plain `<script>` becomes a
  classic IIFE bundle (the extension background script and anything loaded
  without module support).
* `src/shell/background.js` is always bundled for the extension and mobile
  targets even though no page references it.
* Anything under `vendor/` is copied verbatim, never bundled.
  `browser_extension/icons` is copied to every target.
* `{{PLACEHOLDER}}` tokens in HTML (the room page) are left for the server to
  substitute.
* Pages that import `lib/i18n.js` must sit at the target root, because the
  language files are addressed relative to the page.

Bundled code can use these compile-time defines: `__UI_VERSION__` (the
`VERSION` string), `__BRIDGE_VERSION__` (`1`), `__SHELL_TARGET__` (`"ui"`,
`"extension"` or `"mobile"`) and `__I18N_FILES__` (language code to language
file path, hashed for the served UI). The background script imports the
context menu titles from `sealskin-i18n/context-menu`, a module the build
generates under `dist/.generated/` from `background.contextMenu` of every
language, because the menus are registered before any page could fetch a
language file.

### Translations

`src/i18n/<code>.json` holds one language and is the single source for every
target. At build time each is deep-merged over English so the runtime fetches
one file; a missing key falls back to English. To add a language, drop in a
file and rebuild.

The served UI gets the full dictionaries, minified and content-hashed. The
extension and the mobile app must read in the user's language before any
server is configured, so they bundle their own copy, generated at build time
from the same source files: the build scans the HTML pages and JavaScript
that ended up in the shell bundle for `data-i18n*` attributes and quoted
dotted keys, adds the blocks listed whole in `src/shell/i18n-namespaces.json`
(needed for keys assembled at runtime, such as `shell.host.*`), and writes
one pretty-printed `i18n/<code>.json` per language holding only those keys.
Nothing is mirrored by hand: a string added to the connection page or the
host panel is picked up on the next build, and strings the shells never show
stay out of the store packages. The collaboration room keeps its own bundled
translations in `src/ui/room/translation.js` since it is served on its own
route.

Update the translations whenever user-facing strings change.

### Loading the extension unpacked

After `npm run build`:

* **Chrome**: copy `client/dist/extension/manifest.chrome.json` to
  `client/dist/extension/manifest.json`, open `chrome://extensions`, enable
  Developer mode and **Load unpacked** on `client/dist/extension`.
* **Firefox**: copy `manifest.firefox.json` to `manifest.json`, open
  `about:debugging#/runtime/this-firefox` and **Load Temporary Add-on**,
  picking that `manifest.json`.

`browser_extension/build.sh` runs the client build and zips the Chrome and
Firefox packages with the version from `VERSION`; that is what the release
workflows upload and what gets submitted to the stores.

With a self-signed certificate on the server, the extension falls back to
plain HTTP on the API port until you accept the certificate; the host's
**Open server** button opens the right page for that. Firefox requires a
trusted certificate for extension traffic.

## The mobile shells

```bash
cd mobile
npm install
npm run android    # builds dist/mobile, copies it to www/, cap sync, opens Android Studio
npm run ios        # same, then opens Xcode
```

`mobile/ios/` is committed; `mobile/android/` is generated by `build.sh`,
which also produces a signed APK and AAB when the keystore variables are set
(see [Releasing](releasing.md)) and a debug APK otherwise. Android's
`versionCode` is derived from `VERSION` locally and overridden by a build
number in CI. Native plugins: Browser (sessions in a Custom Tab or Safari
view), App (back button), Filesystem, blob writer and file opener
(downloads). The WebView needs a trusted certificate.

## Versioning

`VERSION` at the repository root is read by the client build (stamped into
the served UI manifest, both extension manifests and the mobile package), by
the packaging scripts (artifact names, Android `versionCode`), by
`server/app/version.py` (the API and `/api/ui/version`) and by the wheel
build. [Releasing](releasing.md) explains how a version becomes a release.

## This documentation

The pages are plain Markdown under `docs/content` and are what the Docs
workflow publishes to GitHub Pages. Editing one on GitHub is enough to change
the site: the **Edit on GitHub** button beside every page opens the file it
was built from. A page begins with front matter:

```yaml
---
title: Getting Started
description: One sentence, shown under the title and in search results.
---
```

`docs/content/meta.json` lists the pages in sidebar order; a new page has to
be added there to appear. Links between pages are written the way GitHub
resolves them (`start.md`, `administration.md#gpus`) and rewritten to site
URLs at build time. Images live in `docs/content/assets`.

Previewing the site needs Node.js and, for the Server Reference, `python3`:

```bash
cd docs
npm install
npm run dev          # http://localhost:3000
npm run build        # static site in docs/out
npm run check-links  # every link and anchor in docs/out must resolve
```

Two parts of the site are generated:

* **The Server Reference** is rendered from the docstrings of `server/app`
  by [fumadocs-python](https://fumadocs.dev/docs/integrations/python). It is
  never committed: `docs/content/reference` is gitignored, and `npm run dev`
  and `npm run build` regenerate it, so the published site always matches
  the code that built it. `npm run generate:api` regenerates it on its own.
  The generator bootstraps a private Python venv in `docs/.venv-docs` on
  first use; delete that directory to rebuild it.
* **The Settings Reference** (`docs/content/settings.md`) is rendered from
  `SETTING_DEFINITIONS` by `docs/scripts/generate-settings-doc.py`. Unlike
  the reference it is committed so it reads on GitHub; run
  `npm run generate:settings` after changing a setting and commit the page.
  The Docs workflow runs the script with `--check` and fails when the page
  is stale.

The site is served from the custom domain named in `docs/public/CNAME`
(`docs.sealskin.app`), so the workflow leaves `NEXT_PUBLIC_BASE_PATH` empty.
Without that file it would fall back to the GitHub Pages project path and set
the base path to `/sealskin`, which breaks every asset URL on the custom
domain. The domain configured in the repository's Pages settings must match
the file.
