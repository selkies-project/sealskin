---
title: Authentik
description: Signing in to SealSkin with Authentik, over OpenID Connect, SAML, or the proxy provider's forward-auth.
---

Run with Authentik 2026.8. Authentik sits behind the same proxy at a name of
its own, `authentik.example.com` here.

## OpenID Connect

Create an **OAuth2/OpenID Provider** and an application that uses it; the
application's slug, `sealskin` here, is part of the issuer URL.

| Provider field | Value |
| --- | --- |
| Client type | Confidential |
| Redirect URIs | Strict: `https://sealskin.example.com/api/auth/oidc/callback` |
| Grant types | Authorization Code, Refresh Token |
| Signing Key | Any certificate, as `authentik Self-signed Certificate` |
| Scopes | `openid`, `email`, `profile`, `offline_access` |
| Logout Method | Back-channel |
| Logout URI | `https://sealskin.example.com/api/auth/oidc/backchannel-logout` |

On SealSkin, in the environment or in the dashboard under **Sign In**:

```
SEALSKIN_OIDC_ISSUER=https://authentik.example.com/application/o/sealskin/
SEALSKIN_OIDC_CLIENT_ID=<the provider's client ID>
SEALSKIN_OIDC_CLIENT_SECRET=<the provider's client secret>
SEALSKIN_OIDC_SCOPES=openid profile email offline_access
SEALSKIN_SSO_GROUPS_CLAIM=groups
SEALSKIN_SSO_ADMIN_GROUP=admins
```

The issuer ends in a slash. The user is `preferred_username`, the Authentik
user name, and `groups` comes with the `profile` scope. The groups are
Authentik's, however Authentik fills them: a user it signs in through a
source of its own, as GitHub or Google, has the groups the source's property
mappings give them, and `SEALSKIN_SSO_ADMIN_GROUP` names one of those. A
logout at Authentik ends the SealSkin sign-in at once through the
back-channel URI.

A provider made through Authentik's API starts with no grant types, and
answers every sign-in with `invalid_request` until the two above are set;
one made in the admin interface has them.

## SAML

Create a **SAML Provider** and an application that uses it, slug
`sealskin-saml` here.

| Provider field | Value |
| --- | --- |
| ACS URL | `https://sealskin.example.com/api/auth/saml/acs` |
| Audience | `https://sealskin.example.com/api/auth/saml/metadata` |
| Service Provider Binding | Post |
| Signing Certificate | Any certificate, with **Sign assertions** on |
| SLS URL | `https://sealskin.example.com/api/auth/saml/slo` (binding Redirect) |
| Property mappings | The `authentik default SAML Mapping` set |

```
SEALSKIN_SAML_METADATA_URL=https://authentik.example.com/application/saml/sealskin-saml/metadata/
SEALSKIN_SAML_USERNAME_ATTRIBUTE=http://schemas.goauthentik.io/2021/02/saml/username
SEALSKIN_SAML_GROUPS_ATTRIBUTE=http://schemas.xmlsoap.org/claims/Group
SEALSKIN_SSO_ADMIN_GROUP=admins
```

Authentik names its attributes by URI, and its NameID is a hash of the
user, so the two settings are needed: without them the SealSkin user is that
hash. They name the SAML attributes alone, so OpenID Connect can be set up
beside SAML with its own claims.

## Forward-auth

Create a **Proxy Provider** in mode **Forward auth (single application)**
with the external host `https://sealskin.example.com`, an application that
uses it, and add the application to the embedded outpost (**Applications →
Outposts**). The proxy then asks the outpost about every request; SWAG
ships the two includes for it, `authentik-server.conf` and
`authentik-location.conf`, which the [SWAG](swag.md) configuration has
ready to uncomment.

```
SEALSKIN_PROXY_AUTH_USER_HEADER=X-authentik-username
SEALSKIN_PROXY_AUTH_GROUPS_HEADER=X-authentik-groups
SEALSKIN_PROXY_AUTH_LOGOUT_URL=https://sealskin.example.com/outpost.goauthentik.io/sign_out
SEALSKIN_SSO_ADMIN_GROUP=admins
```

See [A proxy that signs users in](sign-in.md) for the SealSkin side. A
single-application provider guards the web app's name; leave the session
names out of the proxy's sign-in.
