# SealSkin Browser Extension (shell)

Since 0.3.0 the store-published extension is a thin shell. It bundles only what
cannot be served by the server:

* `background.js`: context menus, "Send Next Download" interception, the E2EE
  handshake and encrypted fetch, JWT signing with the stored private key, the
  session-to-tab map, and the Chrome streaming download handler.
* `popup.html`, `options.html`, `host.html` + `host.js`: the frame host. It
  shows the bundled connect page when nothing is configured, otherwise it loads
  the server's web UI (`https://<server>:<session port>/ui/<page>.html`) in an
  iframe and relays the page's bridge requests to the background.
* `connect.html`: the connection page (server address, ports, username, keys,
  config import/export, login test). This is the only UI page that works with
  no server.

Everything else (launcher popup, options dashboard and admin panels, file
manager, upload page, translations, icon font) is served by the server and
updated together with the server container. A store release is only needed when
the manifest or the bridge protocol changes.

## Source and build

The source lives in `../client/src/shell` (shared with the mobile app) and the
bridge contract is documented in `../docs/architecture.md`. Build everything
with:

```bash
cd ../client && npm install && npm run build
```

This writes `../client/dist/extension`. Load it unpacked from that directory
(Chrome: `chrome://extensions` > Load unpacked; Firefox: `about:debugging` >
Load Temporary Add-on, picking `manifest.firefox.json` after copying it to
`manifest.json`). `build.sh` in this directory runs the client build and zips
Chrome and Firefox packages, stamping the version from `../VERSION`.

`manifest.chrome.json` and `manifest.firefox.json` are the only files that stay
here besides the icons.

## Self-signed certificates

Chrome users on a self-signed certificate keep the existing workflow. The
background tries the https session port first and falls back to plain http on
the API port for E2EE traffic. The served web UI loads from whichever origin
the handshake succeeded on. If the iframe cannot load (the certificate has not
been accepted yet), the host shows a "Server unreachable" panel with an
"Open server" button that opens `https://<server>:<session port>/ui/` in a
normal tab so the certificate can be accepted; press Retry afterwards. Firefox
requires a valid certificate for extension traffic, as before.

## Bridge

Served pages never call `chrome.*`. They send `postMessage` requests to the host
page, which validates the sender origin and relays privileged work to the
background. The full request list is in `../docs/architecture.md` section 4.2.
The connect page additionally uses `getConnectConfig`, `saveConfig` and
`clearConfig`, which the host only honours while the bundled connect page is
framed.
