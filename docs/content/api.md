---
title: HTTP API
description: How requests are wrapped and authenticated, and every endpoint the server exposes.
---

The API is what the served UI and the shells speak; it is documented here for
people writing their own client or automation. FastAPI also publishes an
OpenAPI description at `/openapi.json` and an interactive explorer at
`/docs` on the API port, but neither can be used directly because of the
encryption envelope described first.

## Conventions

**Base URL.** `https://<server>:8443` (through Caddy) or
`http://<server>:8000` (the API server directly). Both serve the same
routes.

**Handshake.** Before anything else, establish a crypto session:

```
POST /api/handshake/initiate         -> {"nonce": b64, "signature": b64}
   verify: RSA-PSS(SHA-256, salt 32) over the nonce with the server public key
POST /api/handshake/exchange         {"encrypted_session_key": b64}
   RSA-OAEP(SHA-256) of a 32-byte AES key under the server public key
                                     -> {"session_id": hex}
```

**Envelope.** Every endpoint marked *encrypted* below expects the header
`X-Session-ID: <session_id>` and, for requests with a body,
`{"iv": b64, "ciphertext": b64}` where the ciphertext is AES-256-GCM of the
JSON body with a 12-byte IV and no additional data. JSON responses come back
in the same envelope. Optionally add `X-Idempotency-Key: <random>` to a
non-GET request to make a retry safe.

**Authentication.** Endpoints marked *user* or *admin* require
`Authorization: Bearer <jwt>`, an `RS256` token signed with the user's
private key whose claims include `sub` (the username) and `exp`. *admin*
endpoints additionally require an administrator account.

**Errors.** Standard HTTP status codes with a JSON `{"detail": ...}` body:
`400` for a bad envelope or crypto session, `401` for a bad token, `403` for
a permission the account lacks, `404`, `422` for a body that fails
validation, `500` for provider errors.

## Handshake and status

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /api/handshake/initiate` | none | Signed nonce for server verification. |
| `POST /api/handshake/exchange` | none | Register the wrapped AES key; returns the crypto session id. |
| `POST /api/admin/status` | encrypted, user | The caller's role, effective settings, detected GPUs, CPU model, disk usage and proxy certificate expiry. |
| `GET /api/ui/version` | none | `{"version", "bridge"}`: server version and bridge protocol version. |
| `GET /api/ui/template_schema` | none | The template editor's variable definitions. |

## Applications and launching

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /api/applications` | encrypted, user | Applications the caller may launch, with their capabilities and extensions. |
| `GET /api/app_icon/{app_id}` | encrypted, user | A custom (meta-app) icon as base64 JSON. |
| `POST /api/launch/simple` | encrypted, user | Launch with no context. Body: `application_id`, `home_name?`, `language?`, `timezone?`, `selected_gpu?`, `launch_in_room_mode?`, `wayland_mode?`. |
| `POST /api/launch/url` | encrypted, user | As above plus `url`; the container receives `SEALSKIN_URL`. |
| `POST /api/launch/file` | encrypted, user | As above plus `filename`, `upload_id`, `total_chunks`, `open_file_on_launch?`; the uploaded file is placed in `Desktop/files` and, if requested, `SEALSKIN_FILE` points at it. |
| `POST /api/launch/file_path` | encrypted, user, persistent storage | Launch with a file already in the caller's shared files (`filename`). |

A launch returns `{"session_url", "session_id"}`; `session_url` is relative
to the session port and contains the one-time access token.

## Sessions

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `GET /api/sessions` | encrypted, user | The caller's sessions, newest first. |
| `DELETE /api/sessions/{session_id}` | encrypted, user | Stop one of the caller's sessions. |
| `POST /api/sessions/{session_id}/send_file` | encrypted, user | Move a finished upload (`filename`, `upload_id`, `total_chunks`) into the session's files directory. |
| `GET /{session_id}/` | access token | Exchange `?access_token=` for the session cookie and redirect. Add `&embedded=true` for a `SameSite=None` cookie. |
| `GET /room/{session_id}` | access or collaboration token | The collaboration room page. |
| `WS /ws/room/{session_id}` | collaboration token | The room's WebSocket. |

Everything under `/{session_id}/` other than the initial exchange is proxied
by Caddy to the container after `forward_auth`.

## Home directories

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `GET /api/homedirs` | encrypted, user, persistent storage | The caller's home directories. |
| `POST /api/homedirs` | encrypted, user, persistent storage | Create one (`home_name`, letters, digits, `_`, `-`). |
| `DELETE /api/homedirs/{home_name}` | encrypted, user, persistent storage | Delete one and its contents. |

## Uploads

Uploads are chunked so that large files fit within the encrypted envelope.
Upload ids are lowercase UUIDs scoped to the uploading user.

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /api/upload/initiate` | encrypted, user | `{"filename", "total_size"}` → `{"upload_id"}`. |
| `POST /api/upload/chunk` | encrypted, user | `{"upload_id", "chunk_index", "chunk_data_b64"}`. |
| `POST /api/upload/to_storage` | encrypted, user, persistent storage | Finalise into the shared files of `home_name` (`filename`, `upload_id`, `total_chunks`). |

## Files

All paths are relative to the named home directory (or `_sealskin_shared_files`).

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `GET /api/files/list/{home_dir}?path=&page=&per_page=&search=` | encrypted, user, persistent storage | A page of directory entries. |
| `POST /api/files/create_folder/{home_dir}` | encrypted, user, persistent storage | `{"path", "folder_name"}`. |
| `POST /api/files/delete/{home_dir}` | encrypted, user, persistent storage | `{"paths": [...]}` → a task id; deletion runs in the background. |
| `GET /api/files/delete_status/{task_id}` | encrypted, user | Progress of a deletion. |
| `GET /api/files/download/chunk/{home_dir}?path=&chunk_index=` | encrypted, user, persistent storage | One chunk of a file, base64, with `is_last_chunk`. |
| `POST /api/files/upload_to_dir/{home_dir}` | encrypted, user, persistent storage | Finalise an upload into a directory (`path`, `filename`, `upload_id`, `total_chunks`). |
| `POST /api/files/launch_from_storage` | encrypted, user, persistent storage | Returns a launch context for a server-side file; the client opens the launcher with it. |

## Public shares

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /api/files/share` | encrypted, user, public sharing | `{"home_dir", "path", "password"?, "expiry_hours"?}` → share info including its URL. |
| `GET /api/files/shares` | encrypted, user, public sharing | The caller's shares. |
| `DELETE /api/files/share/{share_id}` | encrypted, user, public sharing | Revoke a share. |
| `GET /public/{share_id}` | none | The file, or a password form. |
| `POST /public/{share_id}` | none | Form field `password`; redirects to a one-time download URL. |
| `GET /public/download/{token}` | none | Download with a one-time token. |

## Administration

All *encrypted, admin*.

| Method and path | Purpose |
| --- | --- |
| `POST /api/admin/data` | Admins, users, groups, server public key, ports and GPUs in one call. |
| `POST /api/admin/admins`, `DELETE /api/admin/admins/{username}` | Create (`username`, `public_key?`) or delete an administrator. |
| `POST /api/admin/users` | Create a user (`username`, `public_key?`, `settings`); returns the generated private key when no key was supplied. |
| `PUT /api/admin/users/{username}` | Replace a user's settings. |
| `DELETE /api/admin/users/{username}` | Delete a user and their storage. |
| `GET`/`POST`/`DELETE /api/admin/users/{username}/homedirs[/{home}]` | A user's home directories. |
| `GET`/`POST`/`DELETE /api/admin/admins/{username}/homedirs[/{home}]` | An administrator's home directories. |
| `POST /api/admin/groups`, `PUT /api/admin/groups/{name}`, `DELETE /api/admin/groups/{name}` | Groups. |
| `GET`/`POST /api/admin/apps/stores`, `DELETE /api/admin/apps/stores/{name}` | App stores. |
| `GET /api/admin/apps/available?url=&store_name=&refresh=` | A store's catalogue, cached unless `refresh`. |
| `GET /api/admin/apps/installed` | Installed apps with image status. |
| `POST /api/admin/apps/installed` | Install from a store entry. |
| `PUT /api/admin/apps/installed/{app_id}` | Replace with a full definition. |
| `PATCH /api/admin/apps/installed/{app_id}` | Partial update: record fields (`users`, `groups`, `app_template`, `auto_update`) apply directly, anything else becomes an override. |
| `DELETE /api/admin/apps/installed/{app_id}` | Uninstall (purges a meta-app's icon and template). |
| `POST /api/admin/apps/installed/{app_id}/check_update` | Compare the local image digest with the registry. |
| `POST /api/admin/apps/installed/{app_id}/pull_latest` | Pull the newest image. |
| `POST /api/admin/apps/meta` | Create a meta-app (`name`, `base_app_id`, `logo`, autostart scripts, `users`, `groups`). |
| `POST /api/admin/launch/meta_customize` | Launch a meta-app with its template mounted read-write. |
| `GET`/`POST /api/admin/apps/templates`, `DELETE /api/admin/apps/templates/{name}` | Templates. |
| `GET /api/admin/sessions` | Every session grouped by user. |
| `DELETE /api/admin/sessions/{session_id}` | Stop any session. |

## Internal

`GET /internal/resolve_session/{session_id}` exists only for Caddy's
`forward_auth`; Caddy refuses it on the public listener and the API server
only receives it over loopback. It answers `200` with `X-Upstream-Host` and
`X-Upstream-Auth` headers, or `403`/`404`.
