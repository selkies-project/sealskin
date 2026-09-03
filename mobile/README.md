# SealSkin Mobile Client (shell)

The mobile client is a Capacitor app. Since 0.3.0 it is a thin shell: it
bundles the connection page and the outer host window, and once a server is
configured it shows the server's web UI (`/ui/popup.html`, `/ui/options.html`,
…) in a WebView iframe. UI changes ship with the server container; the app
only needs a store release when the bridge protocol or native plugins change.

## What runs natively

* `index.html` + `mobile.js` (built from `../client/src/shell/mobile`): the
  outer window. It installs a small `chrome.*` polyfill, runs the same
  `background.js` as the browser extension (E2EE, JWT, pending launch
  context), and hosts the iframe with the bridge relay.
* Native plugins: `@capacitor/browser` (sessions open in a Custom Tab /
  SFSafariViewController), `@capacitor/app` (back button), `@capacitor/filesystem`
  + `capacitor-blob-writer` + `@capawesome-team/capacitor-file-opener`
  (downloaded files are written to the app cache and opened with the system
  viewer).
* `connect.html`: the connection page, the only page that works with no server.

## Functional differences from the browser extension

1. No context menu integration and no download interception; those need a
   browser extension.
2. Sessions open in the system browser (Custom Tab), not inside the app.
3. **A valid TLS certificate is required.** Android and iOS WebViews reject
   self-signed certificates and block mixed content, so the served UI only
   loads over https from a trusted certificate (the Duck DNS installer in
   docker-sealskin produces one).

## Build

```bash
cd ../client && npm install && npm run build   # writes ../client/dist/mobile
cd ../mobile && npm install
npm run android    # copies dist/mobile to www, cap sync, opens Android Studio
npm run ios
```

`build.sh` produces a signed APK in CI and stamps the version and Android
`versionCode` from `../VERSION`.
