---
title: Signing in
description: The web sign-in (the root key, OpenID Connect, SAML, or a reverse proxy), the application keys of the companion apps, the key-file clients, groups and limits, and running behind a proxy.
---

SealSkin takes three kinds of client.

| | Web sign-in | Application key | Key file |
| --- | --- | --- | --- |
| Clients | The web app at `https://<server>/` | Browser extension and mobile app, as companions of the web app | Browser extension and mobile app with a configuration file |
| Who the user is | Root's application key, an identity provider, or a reverse proxy says | A key the user made in the web app names them | The user's RSA key signs every request |
| Transport | HTTPS, with a certificate the browser trusts | HTTPS, with a certificate the device trusts | HTTPS, or encrypted payloads over HTTP on the API port |
| Reaches | Every node of a [cluster](cluster.md) | Every node of a cluster | The one server in the configuration file |

The key-file clients work as they always have and are described in
[Getting started](start.md). **Key Files** under Sign In in the dashboard, or
`SEALSKIN_LEGACY_AUTH=false` on every node, turns them off, with their
encrypted API and the generated `admin.json`; the companion apps then connect
with [application keys](#application-keys), a user made in the dashboard is
sign-in-only, and administrators come from the provider's group or a group's
Administrator switch, with the root key as the way in without one. Setting up
a server is therefore: sign in with the root key, configure the provider under
Sign In, sign in through it as an administrator, then switch key files off
there. The rest of this page is the web sign-in and the keys.

## What the web sign-in needs

* **A trusted certificate.** The web app signs in with a cookie, which a
  browser only keeps for an origin it trusts.
* **The application's web client, from its image.** A session's page is
  the Selkies web client. Served by the container it would be code of the
  image running on the web app's origin, next to the sign-in cookie, so the
  server takes the client out of the image instead, once per image, after
  each pull, and serves it at `/<session id>/` itself; the container answers
  the session's API alone, and nothing it answers may run as a page. The
  images of [linuxserver.io](https://www.linuxserver.io/) keep the client
  under `/usr/share/selkies`; `SEALSKIN_WEB_CLIENT_PATH` names the directory
  for an image that keeps it elsewhere. A session of a web sign-in whose
  client the server could not export does not open.

Session isolation (`SEALSKIN_SESSION_ISOLATION=true`) goes further: every
session of a web sign-in is then served on an origin of its own,
`<session id>.<session domain>`, and nowhere else, so it shares no storage,
cookies, or service workers with the web app or with other sessions. That
takes DNS resolving every such name to the server and a certificate covering
them, `*.<session domain>`, which a certificate authority issues over a DNS
challenge; `SEALSKIN_SESSION_DOMAIN` names the domain when sessions live
under another one than the web app. It is the setting for a server running
images it does not vouch for. A collaboration room of a web sign-in opens on
its session's origin too.

## The built-in administrator

The first start of a server writes the **root key**, an application key of
the built-in administrator `admin`, to `/config/root_key`. Open the web app
and paste it into the **Root key** field: you are signed in as `admin`, an
administrator with no settings. Then delete the file; the server keeps only a
hash of the key, with every other application key in
`cluster/application_keys.yml`. The key file the same first start writes to
`/config/admin.json` is the same account's for the key-file clients, so the
desktops, files, and keys of `admin` are one set whichever way in you take.
`admin` has no identity provider, so their keys are the one kind the web
sign-in takes; any other user's key answers that it signs the companion apps
in alone.

The root key is for setting the server up, for a server with one
administrator and no provider, and for when the identity provider is away:
once a provider is configured, the sign-in page offers the providers alone,
and the key field is reached by adding `#root` to the web app's address,
`https://<server>/#root`. Set `SEALSKIN_ROOT_KEY` to choose the key yourself,
as from a secret manager; it is kept as the key of `admin` named
`environment`, replaced there when the value changes and dropped when the
variable goes. Five wrong keys from one address within a minute close the
form to that address until the oldest ages out. A sign-in with the root key
ends after `SEALSKIN_WEB_SESSION_SECONDS` unused. Signed in, `admin` can make
further keys under **Application Keys** in Settings like any user, and those
sign in both the web app and the companion apps.

Once your administrators sign in through a provider (`SEALSKIN_SSO_ADMIN_GROUP`
or a group with the **Administrator** switch), `SEALSKIN_ROOT_SIGN_IN=false`
switches the root key off on a node's web sign-in: its sign-in page no longer
offers it and the server refuses it. Hiding the field is not what protects
the key, the hash and the lockout are; the switch is for a deployment that
wants no password-like sign-in at all. It is set per node, so if the provider
is ever gone, restart the node with the variable unset and sign in with the
key again. The Sign In panel warns when the key is off and no provider group
names administrators.

## Application keys

The browser extension and the mobile app are companions of the web app: a
user signs in on the web, opens Settings, and under **Application Keys**
makes a key for each device, named after it, which the server shows once:
`ssk_<id>_<secret>@<host>:<port>`, the server's address after the `@`, as
text and as a QR code. The app's connection page takes that key, pasted or,
on a phone, scanned, and is signed in as the user, with everything the web
app allows them, on every node of a cluster. The tab lists each key with when it was made, when it expires,
and when and from which app it was last used, and revokes it; the
Users table of the dashboard shows an administrator every user's keys with
the same. Deleting a user deletes the keys. The server keeps only a hash of
each key, so a key that is lost is made again, never recovered.

A key is a bearer: the app sends it in the `Authorization` header over HTTPS
to the session port, with no handshake and no encrypted envelope, so the
device has to trust the server's certificate. The apps reach the node under
`/companion/`, where the pages and the API are the web app's own and nothing
but a key signs in, so a reverse proxy that signs users in
[passes that path raw](reverse-proxy/sign-in.md#the-companion-apps), or
does not, where the deployment is to be web only.
Key management stays on the web: a key cannot make or revoke keys, so a key
that leaks cannot spread, and revoking it from the web always wins.

What the keys may do is set per user and group, under the same rules as the
[other settings](#groups-switches-and-limits), and binds administrators too:

| Setting | Effect |
| --- | --- |
| **Allow Application Keys** | Off, the user can neither make nor use keys; the apps are refused with `disabled`. |
| **Apps that may use application keys** | Which of the Chrome extension, the Firefox add-on, the Android app, the iOS app, and anything else may use the user's keys. Each app names itself in the `X-SealSkin-App` header and nothing checks the name: this is a convenience, not a lock. A group that names any apps narrows the user's own list to those. |
| **Days an application key lives** | The longest lifetime a user may choose when making a key; the default sets none, and a key then never expires. |
| **Hours after the last web sign-in at which application keys sleep** | A dormancy rule: when the user's last web sign-in is older than this, their keys sleep until they sign in on the web again. |
| **Application Keys Sleep When the Web Sign-In Ends** | The other dormancy rule: the keys work while the web sign-in the provider or the proxy granted lasts, by its own clock, and sleep as soon as it ends, as when the provider announces a logout. |

A sleeping key answers `dormant`. The app shows **Sign in on the web** and
opens the web app with `?wake=1`; the sign-in there, or the browser's
sign-in that still holds, counts as the user's sign-in now, which wakes the
keys, and the web app says so over its home page with a way back to the app.
Both rules read the user's web sign-ins as the server records them on the
user: the time of the sign-in and until when it lasts, which an OpenID
Connect refresh moves on. The keys of `admin` never sleep, since their web
sign-in is a key.

## Landing on the provider

With one provider configured, the web app can skip its own sign-in page: set
`SEALSKIN_AUTO_SIGN_IN=oidc` or `saml`, or choose the provider under
**Landing** in the Sign In panel, and a signed-out browser is sent to the
provider at once. The page a user sees to sign in is then the provider's
own. A provider that refuses or fails sends the browser back with the reason,
and the page shows it with the sign-in page instead of going to the provider
again, so a broken provider never bounces the browser back and forth.

To reach the server's own sign-in page anyway, with the root key field, add
`#root` to the web app's address: `https://<server>/#root`. The same fragment
asks for the root key where a reverse proxy already signs the browser in.

## OpenID Connect

Register SealSkin with the provider as a confidential client (a public one
works with no secret) using the authorization code flow, with the redirect URI
`https://<server>/api/auth/oidc/callback` as the browser reaches the web app,
then set `SEALSKIN_OIDC_ISSUER`, `SEALSKIN_OIDC_CLIENT_ID`, and
`SEALSKIN_OIDC_CLIENT_SECRET`. SealSkin uses PKCE. Where the provider issues
refresh tokens (the `offline_access` scope on some providers, set in
`SEALSKIN_OIDC_SCOPES`), SealSkin checks the sign-in with it every minute
while it is in use and takes the user's groups as the provider names them then;
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

Where a proxy in front of SealSkin authenticates users, as Authelia and
Authentik do in forward-auth mode, SealSkin takes the user from the header
the proxy sets:

```
SEALSKIN_TRUSTED_PROXIES=10.0.0.5
SEALSKIN_PROXY_AUTH_USER_HEADER=Remote-User
SEALSKIN_PROXY_AUTH_GROUPS_HEADER=Remote-Groups
```

There is no sign-in step: the web app opens signed in. The headers are
believed only from an address in `SEALSKIN_TRUSTED_PROXIES`, and only while
the server finds that the proxy sets them itself rather than passing on what
a visitor sends, which it checks by asking its own public address.
[A proxy that signs users in](reverse-proxy/sign-in.md) describes the check,
what to let through, and signing out.

## Who a sign-in is

The user is the value of `SEALSKIN_SSO_USERNAME_CLAIM` (`preferred_username`,
or the SAML attribute `username`, else the NameID), or of the proxy's header.
An OpenID Connect claim the ID token leaves out is read from the provider's
UserInfo endpoint, and a SAML attribute is named by its `Name` or its
`FriendlyName`. Where both protocols are set up and name them differently,
`SEALSKIN_SAML_USERNAME_ATTRIBUTE` and `SEALSKIN_SAML_GROUPS_ATTRIBUTE` name
the SAML attributes on their own.
It has to be a valid SealSkin user name (letters, digits, `_`, `-`); choose a
claim users cannot set for themselves at the provider.

* A name with no user gets one, with the default settings, unless
  `SEALSKIN_SSO_CREATE_USERS=false`, which refuses the sign-in and tells the
  user to ask for an account. A user created this way is held until an
  administrator places them in a group or approves them, unless
  `SEALSKIN_SSO_HOLD_NEW_USERS=false` (see
  [Groups from the provider](#groups-from-the-provider)).
* The first sign-in through each protocol binds the user to the provider's
  account, and another account naming the same user is refused from then on.
* Members of `SEALSKIN_SSO_ADMIN_GROUP` sign in as administrators, and the
  Users table marks them **Administrator**. The names of key-file
  administrators and `admin` are refused.
* The groups the provider names (`SEALSKIN_SSO_GROUPS_CLAIM`, or the proxy's
  groups header) put the user in the SealSkin groups of the same names, and in
  every group that lists one of them under **Identity provider groups**. They
  are read again at each sign-in and each refresh, so leaving a group at the
  provider leaves it here. [Groups from the provider](#groups-from-the-provider)
  has the whole of it.

These settings can also be written for the whole cluster, in the dashboard
under **Sign In**, where they take precedence over each node's
environment. Each card there has a list of providers that fills in what
is the same for everyone who uses that provider, and a **Test** that asks the
provider, or checks the proxy, with what was saved. The client secret is
better left in the environment of the nodes that sign users in: what the
dashboard writes goes to the shared store.

A provider flow, and the sign-in it makes, live on the node that started
them: with several frontends behind one name, the proxy has to keep a
browser on one node (sticky sessions), or users signed in on one answer 401
on the next. A provider's user named like an existing key-file user is that
user, with that user's home directories; the provider's user named like an
administrator or `admin` is refused.

## Groups from the provider

A SealSkin group carries the switches, limits, pools, app access, and PRoot
Apps catalog its members get, and the provider's groups decide who is in it.
With groups `devs` and `editor` made in the dashboard, a user the provider
puts in `devs` is in SealSkin's `devs` at every sign-in with no further setup.
Where the provider's names differ, each group's **Identity provider groups**
field lists the provider groups whose members are in it, so `editor` can be
fed by `okta-content-team`, and one provider group can feed several SealSkin
groups. A group that sets **Administrator** makes its members administrators,
beside `SEALSKIN_SSO_ADMIN_GROUP`. Groups an administrator attaches to the
user by hand are kept and combined with the provider's, so membership can be
granted here as well as there.

A user the sign-in creates who lands in no group is **held**: signed in, but
told to wait for an administrator and refused everything else. The dashboard
marks such users **Awaiting a group** in the Users table, and **Approve**
opens the groups to place them in, with the groups the provider named shown
beside, as does the provider naming a group of theirs, or
`SEALSKIN_SSO_ADMIN_GROUP`, from then on. Approved with no group chosen, the
user is in `default` (see [The default group](#the-default-group)). With
`SEALSKIN_SSO_HOLD_NEW_USERS=false` a new user is put in `default` at once;
users held before stay held until approved, so turning the setting off lets
nobody in by itself. Where the provider sends no groups and every account should
be looked at first, `SEALSKIN_SSO_CREATE_USERS=false` refuses unknown names
instead, and the administrator creates each user in the dashboard, with no
key, before their first sign-in binds the account.

## The default group

A user in no other group is in `default`, a group with no file: it is the
user's own settings, which are the server's defaults where the user's record
sets nothing (GPU, VPN, persistent storage, and application keys allowed; no
limits), and it is listed in the Groups table so that what such a user gets is
in plain view. It cannot be edited or deleted; to change what a user in it
gets, place them in a group or edit the user. Applications, VPN profiles, and
pools may name `default` beside the other groups to reach exactly the users in
no group, where `all` reaches everyone. No group file may be named `default`,
`all`, or `none`.

## Groups, switches, and limits

A user's own settings apply while the user is in no group (`default`). A user
in groups gets each setting from the groups that set it:

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

A proxy in front terminates TLS for the web app's name and the session
names and passes WebSockets; SealSkin is told how browsers reach it:

```
SEALSKIN_PUBLIC_URL=https://sealskin.example.com
SEALSKIN_SESSION_DOMAIN=example.com
SEALSKIN_TRUSTED_PROXIES=10.0.0.5
```

[Behind a reverse proxy](reverse-proxy/index.md) has the whole of it, with
configurations for SWAG, Traefik, Nginx Proxy Manager, and Caddy, and for
Authelia and Authentik as the identity provider.

The API port, `8000`, serves key-file clients over plain HTTP and never the
web sign-in; leave it unpublished where only the web app is used.
