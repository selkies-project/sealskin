---
title: Troubleshooting
description: What a proxy problem in front of SealSkin looks like, from the launch that finds no session name to the sign-in header that is refused, and what causes each.
---

Start with the server log (`docker logs sealskin`) and, for anything about
signing in, the dashboard under **Sign In**.

## Launching

| What you see | Cause |
| --- | --- |
| "This browser cannot reach an address of the session's own" | With session isolation, the session names do not reach SealSkin. Check that `<anything>.<session domain>` resolves to the proxy, that the certificate covers `*.<session domain>`, and that the proxy has the rule for the session names. `curl https://00000000-0000-4000-8000-000000000000.<session domain>/sealskin-origin` answers `sealskin` when all three hold. |
| The same, with the proxy signing users in on the session names | Set `SEALSKIN_SESSION_DOMAIN`. The web app's test of a session name carries no sign-in, so a guarded name refuses it, and the web app takes the domain the server names. |
| The session tab opens and stays blank or keeps reconnecting | WebSockets do not pass. Turn on the proxy's WebSocket support for both the web app's name and the session names. |
| The session tab shows 403 | The proxy changed `Host` or `Origin`. A session answers only on the name it opened on, and only requests its own page makes. |
| "served by the server's copy of the application's web client, which this server could not export" | The server could not take the web client out of the image: the log names why. The directory is `SEALSKIN_WEB_CLIENT_PATH`; on Docker the server runs a container of the image for it, on Kubernetes a pod whose log its Role must let it read. |
| The proxy's 502 or 504 | The upstream is wrong: `https` for port `8443`, `http` for `SEALSKIN_HTTP_PORT`. |

## The plain HTTP listener

| What you see | Cause |
| --- | --- |
| Nothing listens on `SEALSKIN_HTTP_PORT` | `SEALSKIN_TRUSTED_PROXIES` is empty; the log says so at start. |
| 403, "serves the reverse proxies in SEALSKIN_TRUSTED_PROXIES alone, and 172.20.0.9 is not one" | The proxy's address is not the one listed. The message names the address the request came from. |
| 403, "must send X-Forwarded-Proto" | The proxy does not say how the browser reached it. Have it send `X-Forwarded-Proto: https`. |
| A redirect loop to the public URL | The proxy says the browser came over plain HTTP. Either it did, and the proxy's own redirect to HTTPS is missing, or a second proxy in front terminates TLS and this one reports its own side. |

## Signing in

| What you see | Cause |
| --- | --- |
| The proxy's sign-in page, then SealSkin's own | Header sign-ins are refused. **Sign In → Reverse Proxy** says why: `open` (the proxy passes on the header it names as a visitor wrote it: its forward-auth is missing from SealSkin's site, or it does not set the groups header SealSkin reads), `unreachable` (the server cannot reach `SEALSKIN_PUBLIC_URL`), or no trusted proxy. See [the check](sign-in.md#the-headers-are-believed-only-from-a-proxy-that-sets-them). |
| Proxy users are in no groups | `SEALSKIN_PROXY_AUTH_GROUPS_HEADER` is empty, which is its default. |
| Signed in as the wrong kind of user | The administrator group is `SEALSKIN_SSO_ADMIN_GROUP`, matched against the groups header or claim exactly. |
| "Your account's name cannot be a SealSkin user name" | The claim in `SEALSKIN_SSO_USERNAME_CLAIM` is not in the ID token, the UserInfo answer, or the SAML attributes, or its value is not a SealSkin user name (letters, digits, `_`, `-`; an email address is not one). |
| A SAML user whose name is a long hash | The provider's NameID. Name the attribute that holds the user name in `SEALSKIN_SAML_USERNAME_ATTRIBUTE`. |
| Nobody is an administrator and the proxy signs everyone in | The provider names no groups, as Tinyauth's own users have none. Open `/#root`, sign in with the root token, and give a user the administrator switch. |
| Signing out as a proxy's user comes straight back signed in | The proxy's session ended and the provider's did not. Point `SEALSKIN_PROXY_AUTH_LOGOUT_URL` at a page that ends both. |
| "Another account of the identity provider already signs in as this SealSkin user" | The user first signed in from another provider, or another account of this one. Signing in binds a SealSkin user to the provider's account. To move a user, delete the user in the dashboard and let the next sign-in create it again, or empty the `auth` entry (`auth: {}`) in the user's file under `/config/.config/sealskin/keys/users/`. |
| The provider answers "invalid redirect URI" | It was registered for another address than `SEALSKIN_PUBLIC_URL`, port included. The dashboard lists the exact URLs under **Sign In**. |
| The root token form says to wait | Five wrong tokens came from your address within a minute. Behind a proxy the address is the browser's only when the proxy is in `SEALSKIN_TRUSTED_PROXIES`; otherwise every visitor counts as the proxy. |
