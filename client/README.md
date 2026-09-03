# SealSkin client

All web UI source lives here and one build produces every consumer:

| Output | Consumer | Hashed | Minified |
|---|---|---|---|
| `dist/ui` | served by the server under `/ui/` | yes | yes |
| `dist/extension` | contents of the browser extension zip | no | no (store review friendly) |
| `dist/mobile` | Capacitor web dir (`mobile/www`) | no | no |

```bash
cd client
npm install
npm run build            # all targets
npm run build -- --target ui
npm run watch            # rebuild on change under src/
SEALSKIN_BUILD_STRICT=1 npm run build   # fail if any page or entry is missing
```

`browser_extension/build.sh` and `mobile/build.sh` call this build and package
the result; `VERSION` at the repo root is the only version number.

## Layout

```
src/ui/          served pages (popup, options, files, upload) + css/ + room/
src/shell/       extension shell pages (host, connect) and background.js
src/shell/mobile/ mobile host page (index.html) and its script
src/lib/         shared modules: bridge, i18n, crypto-utils, languages, dom, api
src/i18n/        one JSON per language; English is the fallback
vendor/          vendored third party assets (Font Awesome), copied verbatim
```

## How entries are found

The build reads every HTML page of a target and turns each local
`<script src>` and `<link rel="stylesheet" href>` into an esbuild entry, then
rewrites the tag to the emitted file name. Rules:

* `<script type="module" src="x.js">` is bundled as ESM, a plain `<script src>`
  as a classic IIFE bundle (use that for the extension background script and
  anything loaded without module support).
* `src/shell/background.js` is always bundled (IIFE) for the extension and
  mobile targets even though no page references it.
* A reference containing `vendor/` (for example
  `vendor/fontawesome/css/all.min.css`, or `../vendor/...` from a nested page)
  is copied verbatim from `client/vendor/` into `<target>/vendor/` and never
  bundled.
* `browser_extension/icons` is copied to `<target>/icons` for every target.
* `{{PLACEHOLDER}}` tokens in HTML (the room page) are left untouched for the
  server to substitute.
* Pages that import `lib/i18n.js` must sit at the target root (the language
  files are addressed as `i18n/<lang>[.hash].json` relative to the page).

Mobile builds `src/shell/mobile/*.html` plus the shared shell pages listed in
`src/shell/mobile/shared-pages.json` (default `["connect.html"]`).

## Defines available to bundled code

| Name | Value |
|---|---|
| `__UI_VERSION__` | `"0.3.0"` (string from `VERSION`) |
| `__BRIDGE_VERSION__` | `1` (number) |
| `__SHELL_TARGET__` | `"ui"`, `"extension"` or `"mobile"` |
| `__I18N_FILES__` | `{ "en": "i18n/en.9F151282.json", ... }` paths relative to the target root |

## Translations

`src/i18n/<code>.json` holds one language. At build time each language is
deep-merged over English so the runtime fetches exactly one file:

```js
import { loadTranslator, applyTranslations, resolveLanguage } from '../lib/i18n.js';
const t = await loadTranslator(navigator.language);   // t(key, vars) -> string | array
applyTranslations(document.body, t);                    // data-i18n, -placeholder, -title
applyTranslations(el, t, { html: true });              // innerHTML for keys that carry markup
```

The shells receive a subset: only the dotted prefixes in
`src/shell/i18n-namespaces.json` (default `common`, `connect`,
`options.config`, `options.status`, `options.dashboard`, `background`).

To add a language: drop `src/i18n/<code>.json` (any missing key falls back to
English) and rebuild. To add a page: create `src/ui/<name>.html` with its
script/stylesheet tags and rebuild; nothing else to register.

The collaboration room (`src/ui/room/`) keeps its own bundled translations in
`translation.js` because it is served on its own route.

## Extension manifests

`browser_extension/manifest.chrome.json` and `manifest.firefox.json` are copied
into `dist/extension` with `version` set from `VERSION` and the Firefox
`background.scripts` pointed at the bundled `background.js`.
`browser_extension/build.sh` zips the directory once per browser.
