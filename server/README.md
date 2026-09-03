# SealSkin Server

SealSkin is a self-hosted, client-server platform that enables you to run powerful, containerized desktop applications streamed directly to your web browser. It uses a browser extension to intercept user actions—such as clicking a link or downloading a file—and redirects them to a secure, isolated application environment running on your own server.

## Primary Functions

1.  **Remote Application Access:**
    Stream demanding desktop applications (e.g., video editors, IDEs, 3D modeling software, office suites) to any device with a web browser. This allows any file or link on the web to be opened directly in a full-featured application, leveraging your server's hardware (CPU, GPU, RAM) without local installation.

2.  **Secure Browser & File Isolation:**
    Isolate your local machine from the public internet. All web content, downloads, and application processes are executed within a sandboxed container on the remote server. This prevents malware, exploits, and trackers from ever reaching your device, ensuring a clean and secure local environment.

3.  **Centralized Data & File Management:**
    Intercept file downloads and manage persistent storage directly on the server. The integrated file manager allows you to browse, upload, download, and organize your files, making them immediately available to your entire suite of remote applications.

4.  **Real-time Collaboration:**
    Launch applications into a shared "room" where multiple users can interact with the same session. The collaboration suite features integrated, low-latency audio/video chat and shared input controls, making it ideal for pair programming, joint document editing, or collaborative design sessions.

5.  **Secure File Sharing:**
    Create secure, shareable public links for files stored on your server. Links can be protected with a password and set to automatically expire, giving you full control over how you share your data.

## Core Architecture

The system is composed of two primary components communicating over a secure, end-to-end encrypted channel.

### [SealSkin Server](./server/README.md)

The server is the central hub of the platform. It is responsible for all management, orchestration, and traffic proxying. Its architecture separates management tasks from live application traffic to enhance security and stability.

*   **API Server (Control Plane):** Exposes a REST API that handles user authentication, application and session management, file operations, and collaboration coordination. All communication with this API is protected by end-to-end encryption.
*   **Caddy Proxy (Data Plane):** Uses the **Caddy web server** as a robust, high-performance reverse proxy for all live application traffic (HTTP and WebSockets). Caddy handles TLS termination and authenticates every request against the API server, ensuring that internal application containers are never directly exposed to the internet.

### [SealSkin Browser Extension](./browser_extension/README.md)

The browser extension is the user's entry point into the SealSkin environment. It integrates directly into the browser to capture user actions (e.g., right-clicking a link, downloading a file) and provides the UI for launching and managing remote sessions, files, and settings. It is responsible for all client-side cryptographic operations, including the E2EE handshake and signing authentication tokens.

## How It Works: A Typical Workflow

1.  **User Action:** A user right-clicks a link in their browser and selects "Open in Secure Session."
2.  **Extension Capture:** The **Browser Extension** captures this intent, opens its popup UI, and presents a list of compatible remote applications.
3.  **Secure Communication:** The extension establishes an End-to-End Encrypted (E2EE) channel with the server's **API Server**. It then authenticates the user by sending a JSON Web Token (JWT) signed with the user's private key.
4.  **Server Orchestration:** The **API Server** receives the encrypted launch request. After authenticating the user and verifying permissions, it instructs its backend provider (Docker) to launch a new, isolated application container.
5.  **Proxy Connection:** Once the container is running, the server returns a unique, single-use URL pointing to the **Caddy Proxy**.
6.  **Authentication & Proxying:** The extension opens this URL. The Caddy Proxy validates the connection by performing a `forward_auth` sub-request to the API server, which checks the user's secure session cookie. Upon success, Caddy proxies all subsequent traffic between the user and the isolated application container.

## Key Features

*   **End-to-End Encrypted API:** All management communication between the client and server is encrypted, protecting sensitive data like launch parameters and user information.
*   **Passwordless Authentication:** Securely authenticates users via a public/private key cryptographic challenge (signed JWTs), eliminating the need for passwords.
*   **Caddy-Powered Data Plane:** Uses Caddy for robust, high-performance proxying, handling TLS termination and per-request session authentication.
*   **Real-time Collaboration:** Launch applications into a shared room with integrated audio/video chat, shared gamepad/input control, and role-based permissions (controller, participant, read-only).
*   **Integrated File Manager:** The extension includes a UI to browse, upload, download, create folders, and delete files in your persistent server storage.
*   **Secure Public File Sharing:** Create password-protected, expiring links to share files from your server with others.
*   **Containerized Application Backend:** Uses Docker as the primary provider to run applications in isolated, sandboxed environments.
*   **Meta-Applications:** Admins can create customized versions of existing applications with pre-configured settings and files for specific workflows.
*   **Role-Based Access Control (RBAC):** A clear distinction between Admins (full system control) and Users (can only launch and manage their own sessions).
*   **Extensible App Stores:** Add multiple YAML-based application sources ("app stores") to browse and install new applications from.
*   **Full Admin UI:** The extension's options page transforms into a complete management dashboard for administrators, allowing for user, group, and application management directly from the browser.

## Getting Started

### Prerequisites
*   A server with **Python** and **Docker** Installed.
*   **Caddy** installed and available in the system's PATH on the server.
*   An SSL certificate and key for your server's domain.

### 1. Server Setup

**From a wheel (recommended for production):**

```bash
pip3 install sealskin-server
sealskin-server
```

Or install directly from a release asset:

```bash
pip3 install "https://github.com/selkies-project/sealskin/releases/download/v${SEALSKIN_VERSION}/sealskin_server-${SEALSKIN_VERSION}-py3-none-any.whl"
sealskin-server
```

**From source (for development):**

1.  Clone the repository to your server.
2.  Navigate to the `server` directory.
3.  Configure the server by setting the required environment variables. See the **Configuration** section below. At a minimum, you must provide paths to your SSL certificate and key (`SEALSKIN_PROXY_CERT_PATH` and `SEALSKIN_PROXY_KEY_PATH`).
4.  Install requirements `pip3 install -r requirements.txt`.
5.  Run the server `python3 main.py`.

### 2. Browser Extension Setup
1.  Open a Chromium-based browser (e.g., Chrome, Edge, Brave).
2.  Navigate to `chrome://extensions` and enable "Developer mode".
3.  Click "Load unpacked" and select the `browser_extension` directory from this repository.
4.  Open the extension's options page.
5.  Enter your server's domain/IP, API port, and Session port.
6.  Provide your username, private key, and the server's public key to configure the client. The default admin credentials are provided in the server logs on first run.

## Configuration

All settings are environment variables prefixed with `SEALSKIN_`. Every YAML file below is
hand-editable; the server watches the configuration directory and reloads a file when it
changes on disk (`SEALSKIN_WATCH_CONFIG_FILES=false` disables this).

| Environment Variable | Description | Default Value |
| --- | --- | --- |
| `HOST_URL` | When generating the default admin the host to use in this file. | `HOST_URL` |
| `SEALSKIN_LOG_LEVEL` | Logging level (e.g., DEBUG, INFO, WARNING). | `INFO` |
| `SEALSKIN_API_PORT` | Port for the main API server. | `8000` |
| `SEALSKIN_SESSION_PORT` | Port for the session proxy server. | `8443` |
| `SEALSKIN_DEFAULT_PROVIDER` | The default application provider to use. | `docker` |
| `SEALSKIN_APP_RESOURCE_PATH` | URL for the YAML file defining default available applications. | `https://raw.githubusercontent.com/linuxserver/sealskin-apps/refs/heads/master/apps.yml` |
| `SEALSKIN_INSTALLED_APPS_PATH` | Path to the YAML file for installed application records. | `/config/.config/sealskin/installed_apps.yml` |
| `SEALSKIN_APP_STORES_PATH` | Path to the YAML file defining available app stores. | `/config/.config/sealskin/app_stores.yml` |
| `SEALSKIN_APP_TEMPLATES_PATH` | Path to the directory for user-defined application templates. | `/config/.config/sealskin/app_templates` |
| `SEALSKIN_DEFAULT_APP_TEMPLATES_PATH` | Path to the directory for default application templates. | `app/default_templates` |
| `SEALSKIN_UPLOAD_DIR` | Directory for temporary file uploads (one sub-directory per user). | `/storage/sealskin_uploads` |
| `SEALSKIN_SESSION_COOKIE_NAME` | Name of the session cookie. | `sealskin_session_token` |
| `SEALSKIN_AUTOSTART_CACHE_PATH` | Path to cache autostart scripts. | `/config/.config/sealskin/autostart_cache` |
| `SEALSKIN_APP_STORE_CACHE_PATH` | Path to cache app store YAML files. | `/config/.config/sealskin/app_stores_cache` |
| `SEALSKIN_AUTO_UPDATE_APPS` | Enable automatic pulling of the latest app images in the background. | `True` |
| `SEALSKIN_AUTO_UPDATE_INTERVAL_SECONDS` | How often to check for app image updates (in seconds). | `3600` |
| `SEALSKIN_PUID` | Default User ID to run containers as. | `1000` |
| `SEALSKIN_PGID` | Default Group ID to run containers as. | `1000` |
| `SEALSKIN_KEYS_BASE_PATH` | Base directory for admin and user public keys. | `/config/.config/sealskin/keys` |
| `SEALSKIN_GROUPS_BASE_PATH` | Base directory for group definition files. | `/config/.config/sealskin/groups` |
| `SEALSKIN_STORAGE_PATH` | Base directory for user home directories. | `/storage` |
| `SEALSKIN_APP_ICONS_PATH` | Directory for storing custom-uploaded application icons. | `/storage/sealskin_app_icons` |
| `SEALSKIN_HOME_TEMPLATES_PATH` | Base directory for meta-app home directory templates. | `/storage/sealskin_home_templates` |
| `SEALSKIN_CONTAINER_CONFIG_PATH` | Mount point for home directories inside the container. | `/config` |
| `SEALSKIN_SERVER_PRIVATE_KEY_PATH` | Path to the server private key PEM file. | `/config/ssl/server_key.pem` |
| `SEALSKIN_PROXY_KEY_PATH` | Path to the proxy SSL private key file. | `/config/ssl/proxy_key.pem` |
| `SEALSKIN_PROXY_CERT_PATH` | Path to the proxy SSL certificate file. | `/config/ssl/proxy_cert.pem` |
| `SEALSKIN_PUBLIC_STORAGE_PATH` | Directory for storing publicly shared files. | `/storage/sealskin_public` |
| `SEALSKIN_PUBLIC_SHARES_METADATA_PATH` | Path to the YAML file for public share metadata. | `/config/.config/sealskin/public_shares.yml` |
| `SEALSKIN_SHARE_CLEANUP_INTERVAL_SECONDS` | How often to run the cleanup job for expired shares (in seconds). | `600` |
| `SEALSKIN_SESSIONS_DB_PATH` | Path to the YAML file for session persistence. | `/config/.config/sealskin/sessions.yml` |
| `SEALSKIN_CADDYFILE_PATH` | Path to the generated Caddyfile for the proxy. | `/config/.config/sealskin/Caddyfile` |
| `SEALSKIN_UI_PATH` | Directory holding the built web UI served under `/ui`. | `app/ui/` (wheel) or `<repo>/client/dist/ui` (source) |
| `SEALSKIN_TEMPLATE_SCHEMA_PATH` | YAML file describing the environment variables editable in app templates. | `server/app/template_schema.yml` |
| `SEALSKIN_CRYPTO_SESSION_TTL_SECONDS` | Idle lifetime of an E2EE session key before it is discarded. | `86400` |
| `SEALSKIN_WATCH_CONFIG_FILES` | Reload YAML configuration files automatically when they change on disk. | `True` |

### Installed apps: references plus overrides

`installed_apps.yml` stores one record per installed application. A record references the
store entry (`source` and `source_app_id`) and keeps only the fields the administrator changed
under `overrides`. The effective application is the store entry deep-merged with those
overrides, so an updated image tag or extension list in the store applies on the next cache
refresh without re-installing. Records written by earlier versions (full snapshots) are
migrated automatically on first start and the file is rewritten once.

```yaml
- id: 5a8f...                     # generated
  source: SealSkin Apps           # store name from app_stores.yml
  source_app_id: firefox          # id inside that store
  app_template: Default
  users: [all]
  groups: []
  auto_update: true
  home_directories: true
  is_meta_app: false
  overrides:                      # only what differs from the store entry
    name: Work Firefox
    provider_config:
      env:
        - {name: FOO, value: bar}
```

Meta-apps add `base_app_id` and `home_template_name`; their custom name, logo and autostart
scripts live under `overrides`.

### Template schema

`app/template_schema.yml` lists every environment variable the template editor exposes
(name, category, type, default, options). It is served unencrypted at
`GET /api/ui/template_schema`, so adding a variable is a server change and never requires a
client release. Labels and descriptions are looked up by the client under the translation key
`options.appTemplates.settings.<NAME>` and fall back to the variable name.

### Served web UI

The built client (`client/dist/ui`) is mounted at `/ui`. HTML entry points and JSON are sent
with `Cache-Control: no-cache`; every other asset has a content hash in its name and is cached
for a year. `GET /api/ui/version` returns the server version and bridge protocol version. The
collaboration room page is read from `<ui path>/room/room.html`.

## Module layout

```
server/main.py                    thin wrapper: ``from app.main import main; main()``
server/setup.py                   PEP 517 build hook (reads ../VERSION for the wheel)
server/app/__main__.py            enables ``python -m app``
server/app/main.py                entry point: renders the Caddyfile, starts Caddy and uvicorn
server/app/api.py                 FastAPI factory, lifespan, background jobs, file watcher
server/app/settings.py            settings definitions (environment variables), UI path resolution
server/app/version.py             reads VERSION from package dir (wheel) or repo root (source)
server/app/state.py               single container for all in-memory state
server/app/persistence.py         atomic YAML read/write, per-file locks, change watcher
server/app/config_store.py        stores, installed app records + resolution, templates, sessions, shares
server/app/security.py            E2EE handshake, EncryptedRoute, JWT verification, password hashing
server/app/launch.py              build_launch_spec(), launch_application(), session stop/swap
server/app/docker_utils.py        Docker client, self-inspection, GPU detection, image cache
server/app/fsutil.py              filesystem helpers
server/app/providers/             provider interface and the Docker provider
server/app/routers/               one router per area (handshake, applications, launch, sessions,
                                  homedirs, admin, uploads, files, shares, internal, ui)
server/app/collaboration.py       collaboration room page, WebSocket and token fan-out
server/app/Caddyfile.tpl          Caddy configuration template
server/app/template_schema.yml    template editor variable definitions
server/tests/                     pytest suite (run with `pytest` from server/)
```

## Development

```bash
cd server
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt ruff pytest pytest-asyncio
ruff check .
pytest
```

## Docker image note

The recommended approach is to install the wheel from the release assets, which
bundles the built UI, Caddyfile template and VERSION file:

```Dockerfile
ARG SEALSKIN_VERSION=0.3.0
RUN pip install --no-cache-dir \
      "https://github.com/selkies-project/sealskin/releases/download/v${SEALSKIN_VERSION}/sealskin_server-${SEALSKIN_VERSION}-py3-none-any.whl"
```

The `sealskin-server` console script starts the server. The UI is served from
inside the installed package automatically.

To build from source instead (for example when modifying the client), copy
`client/dist` next to `server/`:

```Dockerfile
FROM node:22-alpine AS ui
COPY client /src/client
COPY VERSION /src/VERSION
RUN cd /src/client && npm ci && npm run build

FROM ghcr.io/linuxserver/baseimage-alpine:3.23
# ... install python, caddy, requirements ...
COPY server /opt/sealskin/server
COPY VERSION /opt/sealskin/VERSION
COPY --from=ui /src/client/dist /opt/sealskin/client/dist
```

See `docs/docker-image.md` for more options including the release tarball
download approach.

## Details

For detailed information on each component, please see their respective README files:
*   **[SealSkin Server README](./server/README.md)**
*   **[SealSkin Browser Extension README](./browser_extension/README.md)**
