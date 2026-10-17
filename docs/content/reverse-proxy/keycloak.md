---
title: Keycloak
description: Signing in to SealSkin with Keycloak over OpenID Connect or SAML, with the mappers that carry the user's groups.
---

Run with Keycloak 26.8. Behind a proxy that terminates TLS, Keycloak itself
needs `KC_HOSTNAME=https://keycloak.example.com`, `KC_HTTP_ENABLED=true`,
and `KC_PROXY_HEADERS=xforwarded`. The realm is `home` here.

Both protocols can be set up at once: the SAML attributes below carry the
names SealSkin looks for with no claim settings, and OpenID Connect reads
its own claims.

## OpenID Connect

Create a client under **Clients**:

| Field | Value |
| --- | --- |
| Client type | OpenID Connect |
| Client ID | `sealskin` |
| Client authentication | On |
| Authentication flow | Standard flow |
| Valid redirect URIs | `https://sealskin.example.com/api/auth/oidc/callback` |
| Backchannel logout URL | `https://sealskin.example.com/api/auth/oidc/backchannel-logout` |
| Backchannel logout session required | On |

Then, under the client's **Client scopes**, open its dedicated scope and add
a mapper **Group Membership** with the token claim name `groups`, **Full
group path** off, and the ID token and UserInfo switches on.

```
SEALSKIN_OIDC_ISSUER=https://keycloak.example.com/realms/home
SEALSKIN_OIDC_CLIENT_ID=sealskin
SEALSKIN_OIDC_CLIENT_SECRET=<the secret on the client's Credentials tab>
SEALSKIN_SSO_GROUPS_CLAIM=groups
SEALSKIN_SSO_ADMIN_GROUP=admins
```

The user is `preferred_username`. A logout at Keycloak ends the SealSkin
sign-in at once.

## SAML

Create a second client:

| Field | Value |
| --- | --- |
| Client type | SAML |
| Client ID | `https://sealskin.example.com/api/auth/saml/metadata` |
| Valid redirect URIs | `https://sealskin.example.com/api/auth/saml/acs` |
| Name ID format | username |
| Force POST binding | On |
| Sign assertions | On |
| Client signature required (Keys tab) | Off |
| Assertion Consumer Service POST Binding URL (Advanced) | `https://sealskin.example.com/api/auth/saml/acs` |
| Logout Service Redirect Binding URL (Advanced) | `https://sealskin.example.com/api/auth/saml/slo` |

and two mappers in its dedicated scope:

| Mapper type | Setting |
| --- | --- |
| User Property | Property `username`, SAML attribute name `username` |
| Group list | Group attribute name `groups`, **Single Group Attribute** on, **Full group path** off |

```
SEALSKIN_SAML_METADATA_URL=https://keycloak.example.com/realms/home/protocol/saml/descriptor
SEALSKIN_SSO_ADMIN_GROUP=admins
```
