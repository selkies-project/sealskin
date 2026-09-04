<p align="center">
  <img src="mobile/assets/logo.png" alt="SealSkin" width="160">
</p>

<h1 align="center">SealSkin</h1>

<p align="center"><strong>Your browser is your new computer.</strong><br>
Self-hosted browser isolation and remote application streaming.</p>

<p align="center">
  <a href="https://github.com/selkies-project/sealskin/actions/workflows/ci.yml"><img src="https://github.com/selkies-project/sealskin/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/selkies-project/sealskin/actions/workflows/prerelease.yml"><img src="https://github.com/selkies-project/sealskin/actions/workflows/prerelease.yml/badge.svg" alt="Pre-release"></a>
  <a href="https://github.com/selkies-project/sealskin/actions/workflows/docs.yml"><img src="https://github.com/selkies-project/sealskin/actions/workflows/docs.yml/badge.svg" alt="Docs"></a>
  <a href="https://github.com/selkies-project/sealskin/releases/latest"><img src="https://img.shields.io/github/v/release/selkies-project/sealskin?label=release" alt="Latest release"></a>
  <a href="https://opensource.org/licenses/MPL-2.0"><img src="https://img.shields.io/badge/License-MPL%202.0-brightgreen.svg" alt="License: MPL 2.0"></a>
  <a href="https://discord.com/invite/linuxserver"><img src="https://img.shields.io/discord/354974912613449730?logo=discord&label=Discord" alt="Discord"></a>
</p>

<p align="center">
  <a href="https://chromewebstore.google.com/detail/sealskin-isolation/lclgfmnljgacfdpmmmjmfpdelndbbfhk"><img src="https://img.shields.io/chrome-web-store/v/lclgfmnljgacfdpmmmjmfpdelndbbfhk?logo=googlechrome&logoColor=white&label=Chrome%20Web%20Store" alt="Chrome Web Store"></a>
  <a href="https://addons.mozilla.org/en-US/firefox/addon/sealskin-isolation/"><img src="https://img.shields.io/amo/v/sealskin-isolation?logo=firefox&logoColor=white&label=Firefox%20Add-on" alt="Firefox Add-on"></a>
  <a href="https://apps.apple.com/us/app/sealskin/id6758210210"><img src="https://img.shields.io/badge/App%20Store-iOS-black?logo=apple&logoColor=white" alt="App Store"></a>
  <a href="https://play.google.com/store/apps/details?id=io.linuxserver.sealskin"><img src="https://img.shields.io/badge/Google%20Play-Android-3DDC84?logo=googleplay&logoColor=white" alt="Google Play"></a>
  <a href="https://github.com/linuxserver/docker-sealskin"><img src="https://img.shields.io/docker/pulls/linuxserver/sealskin?logo=docker&logoColor=white&label=linuxserver%2Fsealskin" alt="Docker pulls"></a>
</p>

SealSkin runs desktop applications in isolated containers on a server you
control and streams them to any browser or phone. A browser extension turns
every link, file, download and text selection into something you open
remotely instead of locally, so nothing from the web ever runs on the device
in front of you. It is built on [Selkies](https://github.com/selkies-project/selkies)
and the [LinuxServer.io](https://www.linuxserver.io) application images.

**[Read the documentation](https://selkies-project.github.io/sealskin/)** or
visit **[sealskin.app](https://sealskin.app)**.

## Install

### Clients

| | |
| --- | --- |
| **Chrome, Edge, Brave** and other Chromium browsers | [Chrome Web Store](https://chromewebstore.google.com/detail/sealskin-isolation/lclgfmnljgacfdpmmmjmfpdelndbbfhk) |
| **Firefox** | [Firefox Add-ons](https://addons.mozilla.org/en-US/firefox/addon/sealskin-isolation/) |
| **iPhone and iPad** | [App Store](https://apps.apple.com/us/app/sealskin/id6758210210) |
| **Android** | [Google Play](https://play.google.com/store/apps/details?id=io.linuxserver.sealskin) |

Every [release](https://github.com/selkies-project/sealskin/releases) also
carries the extension zips, the APK, the IPA and the server wheel.

### Server

The server ships as the
[linuxserver/sealskin](https://github.com/linuxserver/docker-sealskin)
container. The quickest path to a working server with a trusted certificate
is its installer, which needs Docker, a free [Duck DNS](https://www.duckdns.org/)
subdomain and its token:

```bash
mkdir sealskin && cd sealskin
bash <(curl -sSL https://raw.githubusercontent.com/linuxserver/docker-sealskin/refs/heads/master/install.sh)
```

Then import the generated `config/admin.json` into a client and launch your
first application. The
[Getting Started](https://selkies-project.github.io/sealskin/start) guide
covers the installer, the plain `docker compose` alternative, certificates
and the first login.

## What it does

* **Isolation.** Links, files, downloads and searches open in a fresh
  container on the server. Cleanroom sessions leave nothing behind;
  persistent home directories keep what you choose.
* **Any application.** Browsers, office suites, IDEs, media editors,
  emulators and 3D tools from the app stores, or any Selkies-compatible image
  you add, with NVIDIA and DRI3 GPU acceleration.
* **Files stay on the server.** A file manager, chunked uploads, intercepted
  downloads and password-protected public share links.
* **Collaboration rooms.** Launch any app into a room with chat, voice and
  video, gamepad slots and hand-over of mouse and keyboard control.
* **End-to-end encrypted, passwordless.** Every API call is encrypted with a
  per-session key negotiated against the server's RSA key; users authenticate
  with a signed token from a private key that never leaves the client.
* **One UI, served by the server.** The extension and the app are thin
  shells; the launcher, dashboard and admin panels ship with the server
  image, so UI updates never wait for a store review.

## Documentation

| | |
| --- | --- |
| [Getting Started](https://selkies-project.github.io/sealskin/start) | Install the server, connect a client, launch an application. |
| [Usage](https://selkies-project.github.io/sealskin/usage) | The launcher, context menus, sessions, storage, files and rooms. |
| [Administration](https://selkies-project.github.io/sealskin/administration) | Users, groups, app stores, templates, the App Laboratory, GPUs. |
| [Configuration](https://selkies-project.github.io/sealskin/configuration) | What lives in `/config` and `/storage`, keys, hand-editing the YAML. |
| [Settings Reference](https://selkies-project.github.io/sealskin/settings) | Every environment variable the server reads. |
| [Architecture](https://selkies-project.github.io/sealskin/architecture) | Control and data planes, encryption, the served UI and the shells. |
| [HTTP API](https://selkies-project.github.io/sealskin/api) | Every endpoint and how requests are wrapped. |
| [Development](https://selkies-project.github.io/sealskin/development) | Running from source, building the client, the mobile shells, this site. |
| [Releasing](https://selkies-project.github.io/sealskin/releasing) | Versioning, release notes and the workflows. |
| [Troubleshooting](https://selkies-project.github.io/sealskin/faq) | Certificates, Firefox, mobile, GPUs and Docker. |

The pages live in [`docs/content`](docs/content) and can be edited on GitHub;
the site is rebuilt on every push to `main`.

## Repository

```
VERSION              the one version number for server, UI, extension and app
server/              Python API server, Caddy template, tests, wheel
client/              web UI source and build (dist/ui, dist/extension, dist/mobile)
browser_extension/   manifests, icons and packaging for the extension shell
mobile/              Capacitor project for the iOS and Android shells
docs/                the documentation site
release-notes/       one file per stable release
```

```bash
cd client && npm install && npm run build   # served UI + extension + mobile web dir
cd server && pip install -r requirements.txt && pytest
cd docs && npm install && npm run dev       # documentation at http://localhost:3000
```

## Community

* [Issues](https://github.com/selkies-project/sealskin/issues) for bugs and feature requests
* [LinuxServer.io Discord](https://discord.com/invite/linuxserver) for questions and support
* [linuxserver/sealskin-apps](https://github.com/linuxserver/sealskin-apps), the default application catalogue

SealSkin is licensed under the [Mozilla Public License 2.0](LICENSE). The
privacy policy for the published clients is [PRIVACY.md](PRIVACY.md).
