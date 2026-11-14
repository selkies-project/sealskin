# Threat model

## What this project does and where untrusted input enters
- SealSkin is a self-hosted platform that launches desktop applications as per-session containers (Selkies
  images) on Docker or Kubernetes, on one server or a cluster of them, and streams them to browsers: a FastAPI
  control plane (`server/app`), a Caddy data plane on :8443 with a peer listener on :8444 (`Caddyfile.tpl`), and
  clients built from `client/` (the web app the server serves at `/`, an MV3 browser extension, a Capacitor
  mobile shell).
- Principals, weakest first: an anonymous internet client (the web app's pages, the sign-in flows, public shares,
  the key-file handshake, the template schema and version); a user signed in on the web (a cookie naming a
  record, from the root key, OpenID Connect, SAML, or a trusted reverse proxy's header, who reaches the
  applications, VPN profiles, and pools whose lists name `all`, one of their groups, or `default` for a user in
  no group, and nothing but the status route while held for an administrator); a companion app holding
  an application key (a bearer over `/companion/`); a key-file client (an RS256 JWT the client signs, under
  `SEALSKIN_LEGACY_AUTH`); a template editor (may edit templates but not their `DOCKER_*` and `KUBERNETES_*` settings nor
  the session's identity variables); session collaborators (controller, viewer and read-only invite tokens); an
  admin (who also lays out the dashboards and defines their widgets, any address the node reaches included);
  another node of the cluster (a signed peer request, acting for a user it signed in); the session
  container workload; a VPN gateway; the page inside the shell's iframe (no key material, a postMessage bridge
  only); an administrator's frame widget and the site an embed widget shows (a sandboxed frame inside the home
  page, an origin of its own, one message channel that carries the widget's data in); the documents widgets read
  (feeds, calendars, API answers: a stranger's text).
- Entry points: the sign-in routes (`routers/sso.py`, `proxy_auth.py`, `application_keys.py`), every other
  `/api` route behind `verify_token` (`security.py`, which decides the lane: web cookie, application key, proxy
  header, key file, or peer), the admin and template routes (`admin.py`), app stores and PRoot Apps catalogs
  fetched from configured URLs and parsed as YAML (`config_store.py`, `prootapps.py`), session launch
  (`launch.py`, `providers/`), the file manager, uploads, and public shares, the object store a cluster shares
  (`store.py`, S3 or a file emulation, with users' files under `filesync.py`/`filecrypt.py`), the peer routes
  (`routers/peer.py`, `cluster.py`), VPN profiles an administrator uploads (`vpn.py`, `vpn_kube.py`,
  `vpn/entrypoint.sh`), the web client exported from an image (`webclient.py`), the credential files a node
  reads and the pull secrets it reads on Kubernetes (`registries.py`: a Docker `config.json` and a netrc under
  `/config`, sent to the registries and hosts they name and nowhere else, never to the store or a client),
  Caddy's `forward_auth` to the loopback-only `/internal/resolve_session`, the address a user hands one of
  their own running sessions (`POST /api/sessions/<id>/open_url`: a web address by pattern, passed to a fixed
  script inside the session's container as an argument, run as the session's own user), the brand a page request
  wears (`routers/ui.py`: picked by the `Host` the browser named and, for a web sign-in, the groups of the user
  its cookie names; a brand's name and pictures are public, so the choice gives a stranger nothing but which
  brand a host name wears), the dashboards and the widget library (`dashboards.py`, `routers/dashboards.py`:
  links held to `http`, `https`, and the schemes `dashboard_link_schemes` lists, never one a browser runs in
  the page), the widgets' fetches (`widgets.py` through `netguard.py`: an administrator's address is fetched as
  given, a user's only when every address it resolves to is public, pinned, at every redirect, with no
  credential; a site's own icon for a tile is read the same way and re-encoded as a PNG, as is every icon an
  administrator uploads; icon pack names resolve to fixed hosts alone; whether a link's address answers is
  checked for an administrator's link that asks for it and told to the users its dashboard names alone, never
  for a user's own link), and the widget secrets a node reads from `widget_secrets_path` and
  `SEALSKIN_WIDGET_SECRET_*` (filled in on the server, never sent to a client or written to the store).

## Components that matter most / least
- Most: anything that lets a lower principal reach a higher one: an anonymous client reaching the API, a session
  or a share; a user reaching another user's home, sessions, files in the store, or keys; a session page or
  container reaching the web sign-in's cookie, a proxy's cookie, or the control plane; a template editor or a
  store's YAML reaching Docker or Kubernetes authority (mounts, privileges, devices, networks, the session's
  user); a node, or something posing as one, acting for users or reading the store's secrets; a VPN profile's
  file reaching the gateway's shell; a page in the iframe reaching the key or the extension's privileges; a
  user's link, widget, or imported page, a widget's frame, or a document a widget read running script on the web
  app's origin or reaching its storage; a user's widget making the server fetch an address inside its network; a
  widget's secret or a widget's data reaching a user the widget does not name.
- In scope but lower: the Caddy configuration, the mobile shell, the Kubernetes manifests, CI workflows.
- Out of scope: `docs/` (the documentation site) and the Selkies images the sessions run.

## How to exercise it
- `pytest server/tests` runs offline: the sign-in flows, application keys, the handshake, JWT and AES-GCM lanes
  under FastAPI's TestClient, the peer protocol and store conflicts, file sync, launch specifications on both
  providers with fake clients, VPN gateways, public routes, path traversal and scrypt checks; Docker and
  Kubernetes are never touched.
- `cd client && npm run build` builds the three client targets.

## How you rate severity
- Critical: an anonymous client gaining an account, a session, files or Docker or Kubernetes authority; any path
  from a user, a template editor, a store's YAML, or a VPN profile to that authority; a stranger acting as a node.
- High: one user reaching another's files, sessions, store objects or keys; a session container, a session page,
  or an iframe page reaching the web sign-in, the control plane or the user's private key; authentication bypass
  of a collaboration role; a secret of the store readable by a node that should not hold it; a node's registry
  or host credential reaching a client, the store, another node, or a host the file does not name; a widget
  secret reaching a client or the store; script from a dashboard link, a widget's data, or a widget frame
  running on the web app's origin; a user's widget fetching an address that is not of the public internet.
- Medium: denial of service by an authenticated user; cross-origin reads of non-secret data; secrets in logs.
- Low: hardening without a demonstrated crossing.

## Anything to leave alone
- An admin controls the host's Docker, or the namespace on Kubernetes, by design.
- An admin's widget may fetch any address the node reaches, and an admin's frame widget may run any script
  inside its sandbox, by design. The dashboard lock and the `dashboard_*` user settings shape the page a user
  sees alone and are no boundary; the server's checks on widget data and on a user's own widgets are.
- The bootstrap key file in `/config/admin.json` and the root key in `/config/root_key`, both credentials of the
  built-in administrator `admin`, are documented and meant to be copied and removed by the operator.
- A node with the bucket's credentials is a member of the cluster by design ("the bucket keys are the master
  key"); a file-store cluster approves nodes by hand.
- Reports should name the file and line, give a reproducer (a pytest test against the TestClient is ideal), and
  propose a patch.
