---
title: Signing in
description: The web sign-in (root token, OpenID Connect, SAML, or a reverse proxy), the key-file clients, groups and limits, and running behind a proxy.
---

SealSkin takes two kinds of client.

| | Web sign-in | Key file |
| --- | --- | --- |
| Clients | The web app at `https://<server>/ui/` | Browser extension and mobile app with a configuration file |
| Who the user is | The root token, an identity provider, or a reverse proxy says | The user's RSA key signs every request |
| Transport | HTTPS, with a certificate the browser trusts | HTTPS, or encrypted payloads over HTTP on the API port |
| Reaches | Every node of a [cluster](cluster.md) | The one server in the configuration file |

The key-file clients work as they always have and are described in
[Getting started](start.md). `SEALSKIN_LEGACY_AUTH=false` turns them off, with
their encrypted API and the generated `admin.json`. The rest of this page is
the web sign-in.

## What the web sign-in needs

* **A trusted certificate.** The web app signs in with a cookie, which a
  browser only keeps for an origin it trusts.
* **Names for sessions.** A session of a web sign-in is served on an origin of
  its own, `<session id>.<name>`, and nowhere else, so nothing a session's
  page runs can use the cookie of the web app next to it. DNS has to resolve
  every such name to the server and the certificate has to cover them:
  `*.<server name>`, or `*.<parent name>` as the installer's Duck DNS
  certificate does. The web app probes for a name it can reach before it
  opens a session. `SEALSKIN_SESSION_DOMAIN` names the domain when sessions
  live under another one than the web app.

A collaboration room of a web sign-in opens on its session's origin too.

## The root administrator

The first start of a server writes a root token to `/config/root_token`. Open
the web app, choose **Root token**, and paste it: you are signed in as `root`,
an administrator with no file and no settings. Then delete the file; the
server keeps only a hash of the token, in `cluster/root.yml`.

`root` is for setting the server up and for when the identity provider is
away. Set `SEALSKIN_ROOT_TOKEN` to choose the token yourself, as from a
secret manager; changing it there replaces the hash at the next start. Five wrong tokens from one address within a minute close the form to that
address until the oldest ages out. A root
sign-in ends after `SEALSKIN_WEB_SESSION_SECONDS` unused.

## OpenID Connect

Register SealSkin with the provider as a confidential client (a public one
works with no secret) using the authorization code flow, with the redirect URI
`https://<server>/api/auth/oidc/callback` as the browser reaches the web app,
then set `SEALSKIN_OIDC_ISSUER`, `SEALSKIN_OIDC_CLIENT_ID`, and
`SEALSKIN_OIDC_CLIENT_SECRET`. SealSkin uses PKCE. Where the provider issues
refresh tokens (the `offline_access` scope on some providers, set in
`SEALSKIN_OIDC_SCOPES`), SealSkin checks the sign-in with it every minute
while it is in use and takes the user's groups from each new ID token;
otherwise the sign-in ends when its ID token expires. Register
`/api/auth/oidc/backchannel-logout` as the back-channel logout URL, or
`/api/auth/oidc/frontchannel-logout` as the front-channel one, for a logout at
the provider to end the sign-in at once.

## SAML

Set `SEALSKIN_SAML_METADATA_URL` to the provider's metadata, and register
SealSkin with the provider from `https://<server>/api/auth/saml/metadata`: its
entity ID is that URL, assertions go to `/api/auth/saml/acs` over HTTP-POST,
and single logout to `/api/auth/saml/slo`. The provider signs the response or
the assertion; encrypted assertions are not supported. The sign-in lasts until
the assertion's `SessionNotOnOrAfter`, or until the provider sends a signed
logout request.

Both protocols reuse the provider's own session, so a user signed in there
goes straight through. `SEALSKIN_SSO_FORCE_LOGIN=true` makes the provider ask
for credentials every time. Every sign-in ends after
`SEALSKIN_SSO_MAX_AGE_SECONDS` at the latest.

## A reverse proxy that signs users in

Where a proxy in front of SealSkin authenticates users, as Authentik,
Authelia, or oauth2-proxy do in forward-auth mode, SealSkin takes the user
from the header the proxy sets:

```
SEALSKIN_TRUSTED_PROXIES=10.0.0.5,172.16.0.0/12
SEALSKIN_PROXY_AUTH_USER_HEADER=Remote-User
SEALSKIN_PROXY_AUTH_GROUPS_HEADER=Remote-Groups
```

The header is believed only on a connection from an address in
`SEALSKIN_TRUSTED_PROXIES`, so the proxy has to be the only way in from those
addresses, and it has to remove the headers from what clients send. There is
no sign-in step: the web app opens signed in.

## Who a sign-in is

The user is the value of `SEALSKIN_SSO_USERNAME_CLAIM` (`preferred_username`,
or the SAML attribute `username`, else the NameID), or of the proxy's header.
It has to be a valid SealSkin user name (letters, digits, `_`, `-`); choose a
claim users cannot set for themselves at the provider.

* A name with no user gets one, with the default settings, unless
  `SEALSKIN_SSO_CREATE_USERS=false`.
* The first sign-in through each protocol binds the user to the provider's
  account, and another account naming the same user is refused from then on.
* Members of `SEALSKIN_SSO_ADMIN_GROUP` sign in as administrators. The names
  of key-file administrators and `root` are refused.
* The groups the provider names (`SEALSKIN_SSO_GROUPS_CLAIM`, or the proxy's
  groups header) put the user in the SealSkin groups of the same names, and in
  every group that lists one of them under **Identity provider groups**. They
  are read again at each sign-in and each refresh, so leaving a group at the
  provider leaves it here.

These settings can also be written for the whole cluster, in the dashboard
under **Sign In**, where they take precedence over each node's
environment. The client secret is better left in the environment of the nodes
that sign users in: what the dashboard writes goes to the shared store.

## Groups, switches, and limits

A user's own settings apply while the user is in no group. A user in groups
gets each setting from the groups that set it:

* A **switch** (active, persistent storage, public sharing, GPU, GPU sharing,
  template editing, moving home directories, administrator, and the two
  hardening switches) takes its restricting value when any group gives it
  that. One group that denies the GPU outweighs any number that allow it; one
  group that turns hardening on outweighs any that turn it off.
* A **limit** takes the smallest value any group sets.
* A setting no group sets stays the user's own.

A group leaves a setting alone by not setting it, which is how a group that
only grants one thing is written.

| Limit | Meaning |
| --- | --- |
| `session_limit` | Sessions at once, counted on every node. |
| `session_cpus`, `session_memory_mb` | Cap on each session's container, below what the app or its template asks. |
| `session_hours` | A session this old is stopped. |
| `allowance_hours`, `allowance_period` | Session hours per day, week, or month (UTC), weighted by the [pool's cost](cluster.md#pools). A user out of allowance starts nothing new. |
| `storage_limit` | Gigabytes in the user's directories on a node; at the limit, uploads and persistent launches are refused. |

A negative limit is no limit. Administrators are under none.

## Behind a reverse proxy

Point the proxy at the session port over HTTPS (`https://<node>:8443`; the
certificate there may be the self-signed one if the proxy does not verify
it), pass WebSocket upgrades, and send the wildcard session names to the same
place. Then tell SealSkin how browsers reach it:

```
SEALSKIN_PUBLIC_URL=https://sealskin.example.com
SEALSKIN_SESSION_DOMAIN=apps.example.com        # when sessions are not under sealskin.example.com
SEALSKIN_TRUSTED_PROXIES=10.0.0.5
```

`SEALSKIN_PUBLIC_URL` is what identity provider redirects are built from.
`SEALSKIN_TRUSTED_PROXIES` makes the server take the client's address from
the proxy's `X-Forwarded-For`. The proxy has to pass the browser's `Host`,
`Origin`, and `Sec-Fetch-*` headers unchanged: the server tells a session's
origin by its host name and refuses requests the browser marks as coming from
another origin.

The API port, `8000`, serves key-file clients over plain HTTP and never the
web sign-in; leave it unpublished where only the web app is used.
