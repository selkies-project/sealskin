# Threat model

## What this project does and where untrusted input enters
- SealSkin is a self-hosted platform that launches desktop applications as per-session Docker containers (Selkies
  images) and streams them to thin clients: a FastAPI control plane (`server/app`), a Caddy data plane on :8443
  (`Caddyfile.tpl`), and clients built from `client/` (a served UI, an MV3 browser extension, a Capacitor mobile
  shell).
- Principals, weakest first: an anonymous internet client (the handshake, public shares, static UI, template schema
  and version); an authenticated user (an RS256 JWT the client signs, checked against that user's public key); a
  template editor (may edit templates but not their `DOCKER_*` settings, which carry Docker host authority);
  session collaborators (controller, viewer and read-only invite tokens); an admin; the session container
  workload; the page inside the shell's iframe (no key material, a postMessage bridge only).
- Entry points: the unencrypted handshake (`routers/handshake.py`), every other `/api` route behind `verify_token`
  and the AES-GCM envelope (`security.py`), the admin and template routes (`admin.py`), app stores fetched from
  configured URLs and parsed as YAML (`config_store.py`), session launch (`launch.py`,
  `providers/docker_provider.py`), the file manager, public shares, and Caddy's `forward_auth` to the loopback-only
  `/internal/resolve_session`.

## Components that matter most / least
- Most: anything that lets a lower principal reach a higher one: an anonymous client reaching the API, a session or
  a share; a user reaching another user's home, sessions or keys; a template editor or a store's YAML reaching
  Docker host authority (mounts, privileges, devices, networks); a session container reaching the control plane;
  a page in the iframe reaching the key or the extension's privileges.
- In scope but lower: the Caddy configuration, the mobile shell, CI workflows.
- Out of scope: `docs/` (the documentation site) and the Selkies images the sessions run.

## How to exercise it
- `pytest server/tests` runs offline: the full handshake, JWT and AES-GCM flows under FastAPI's TestClient
  (`test_api_smoke.py`), public routes, path traversal and scrypt checks, and launch specifications; Docker is never
  touched.
- `cd client && npm run build` builds the three client targets.

## How you rate severity
- Critical: an anonymous client gaining an account, a session, files or Docker host authority; any path from a
  user, a template editor or a store's YAML to Docker host authority.
- High: one user reaching another's files, sessions or keys; a session container or iframe page reaching the
  control plane or the user's private key; authentication bypass of a collaboration role.
- Medium: denial of service by an authenticated user; cross-origin reads of non-secret data; secrets in logs.
- Low: hardening without a demonstrated crossing.

## Anything to leave alone
- An admin controls the host's Docker by design.
- The bootstrap admin key in `/config/admin.json` is documented and meant to be removed by the operator.
- Reports should name the file and line, give a reproducer (a pytest test against the TestClient is ideal), and
  propose a patch.
