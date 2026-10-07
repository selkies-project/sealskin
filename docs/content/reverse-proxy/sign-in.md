---
title: A proxy that signs users in
description: Forward-auth in front of SealSkin, where the proxy's header names the user, the check that the proxy removes a visitor's own header, and what to let through.
---

Authelia, Authentik, and others guard a site through the proxy: the proxy
asks them about each request (forward-auth), sends a visitor with no sign-in
to their page, and adds headers that name the user to what it passes on.
SealSkin takes the user from those headers.

```
SEALSKIN_TRUSTED_PROXIES=172.20.0.2
SEALSKIN_PROXY_AUTH_USER_HEADER=Remote-User
SEALSKIN_PROXY_AUTH_GROUPS_HEADER=Remote-Groups
SEALSKIN_SSO_ADMIN_GROUP=admins
```

| Provider | User header | Groups header |
| --- | --- | --- |
| [Authelia](authelia.md) | `Remote-User` | `Remote-Groups` (comma-separated) |
| [Authentik](authentik.md) | `X-authentik-username` | `X-authentik-groups` (separated by `\|`) |
| [Tinyauth](tinyauth.md) | `Remote-User` | `Remote-Groups` (empty for its own users) |
| [oauth2-proxy](oauth2-proxy.md) | `X-Auth-Request-Preferred-Username` | `X-Auth-Request-Groups` |

Name only headers the proxy sets for SealSkin's site. The groups header has
no default: a header the proxy does not set is one a signed-in visitor can
write, and with it their own groups.

The web app opens signed in, with no sign-in page of its own. A name with no
SealSkin user gets one at its first request, members of
`SEALSKIN_SSO_ADMIN_GROUP` are administrators, and groups map as for every
[web sign-in](../signin.md#who-a-sign-in-is). The header settings can also
be written in the dashboard under **Sign In**; set the administrator group
in the environment for the first start, since the dashboard opens to
administrators.

The `root` token still signs in, once the browser is past the proxy: open
`https://sealskin.example.com/#root`, or choose **Root token** in the
account menu. That is also how administrators are appointed where the
provider names no groups: as `root`, give a user the administrator switch.
Signing out as `root` returns to the proxy's user.

When the proxy's sign-in runs out under an open web app, the web app
reloads, and the proxy sends the browser to its sign-in page.

## The headers are believed only from a proxy that sets them

A header is something any visitor can send. The proxy's forward-auth
replaces the ones it manages with the provider's answer, or removes them.
Two mistakes at the proxy undo that, and each is a line or two of its
configuration:

* The forward-auth is left out of SealSkin's site. A visitor's own header
  passes, and anyone who sends `Remote-User: <an administrator>` is that
  administrator.
* The proxy sets the user header and not the groups header SealSkin was told
  to read. A signed-in user who sends `Remote-Groups: admins` is in that
  group.

So SealSkin does not take the proxy on trust. The headers count only when:

* the connection comes from an address in `SEALSKIN_TRUSTED_PROXIES`;
* that address is not a session's, since sessions share the server's
  network;
* **the check passes.** The server sends its own public address,
  `SEALSKIN_PUBLIC_URL`, requests whose sign-in headers hold a made-up
  value, and looks at what arrives: one with no sign-in, and one with the
  cookies of the request it is about to believe, which are the proxy's own
  sign-in. The web app sends the same from the browser of every user the
  proxy signs in.

| The check finds | Meaning | Header sign-ins |
| --- | --- | --- |
| `guarded` | The proxy answered with its sign-in page or a refusal, or passed the requests on with the made-up values gone. | Accepted |
| `open` | A made-up value arrived: the proxy does not set that header for this site. | Refused |
| `unreachable` | The server could not reach its public address. | Refused |

The dashboard shows the state under **Sign In**, with a **Test** button that
runs the check again, and the server log names the header at every change.
A passing check is repeated every minute while the web app is in use; a
failing one is tried again after 15 seconds, and a made-up value that
arrived from a browser holds for five minutes or until **Test** finds it
gone.

A server that cannot reach its public address, behind a router that does not
turn its own address around, asks the proxy directly instead when
`SEALSKIN_TRUSTED_PROXIES` names it by a single address: under the public
name, on the public port and on 443. Where neither route exists,
`SEALSKIN_PROXY_AUTH_UNCHECKED=true` accepts the headers without the check.
Set it only after confirming by hand that forged headers do not get in, with
no sign-in and with the proxy's cookie of a signed-in browser:

```bash
curl -i -H 'Remote-User: admin' -H 'Remote-Groups: admins' https://sealskin.example.com/api/auth/proxy
curl -i -H 'Remote-User: admin' -H 'Remote-Groups: admins' -b 'authelia_session=...' https://sealskin.example.com/api/auth/proxy
```

A proxy that sets the headers answers the first with a redirect or a 401,
or with empty `"user"` and `"groups"`, and the second with the user and
groups of the cookie. `"user":"admin"` or `"groups":"admins"` in either
answer, for a visitor who is neither, is an open one.

## Which names to guard

Guard the web app's name. With [session isolation](index.md#session-isolation)
there are session names too, and for those there is a choice:

* **Left out of forward-auth.** A session answers nobody without the token
  it was launched with, which lives in a cookie of that one name, so the
  names are not open by being unguarded. This is the simpler setup, and the
  one where guests can join a collaboration room from a link.
* **Guarded too.** It works where the provider's cookie covers the session
  domain, as an Authelia cookie for `example.com` covers
  `<session id>.example.com`. Set `SEALSKIN_SESSION_DOMAIN`: the web app
  finds the session names by asking one for an answer with no credentials,
  which a guarded name refuses, and takes the named domain as it is when
  none answers. Room guests then need an account at the provider.

## What to let through

* **Public share links** are `/public/...` on the web app's name. Let that
  path through without a sign-in where users share files with people outside;
  the provider pages show the rule. A request let through this way reaches
  SealSkin without the sign-in headers, which the proxies here remove from
  it.
* **The key-file clients** cannot pass a proxy's sign-in. Use the web app, or
  turn them off with `SEALSKIN_LEGACY_AUTH=false`.

## Signing out

The sign-in is the proxy's, so SealSkin cannot end it. Set
`SEALSKIN_PROXY_AUTH_LOGOUT_URL` to the page that does, and **Sign out**
opens it; left empty, the web app shows no sign-out.

| Provider | Logout URL |
| --- | --- |
| Authelia | `https://auth.example.com/logout` |
| Authentik | `https://sealskin.example.com/outpost.goauthentik.io/sign_out` |
| Tinyauth | `https://tinyauth.example.com/logout` |
| oauth2-proxy | `https://sealskin.example.com/oauth2/sign_out` |
