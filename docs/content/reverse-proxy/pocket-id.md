---
title: Pocket ID
description: Signing in to SealSkin with Pocket ID over OpenID Connect.
---

Run with Pocket ID 2.18. Pocket ID signs users in with passkeys and speaks
OpenID Connect; behind a proxy it needs `APP_URL=https://pocket.example.com`
and `TRUST_PROXY=true`.

Under **OIDC Clients**, add a client:

| Field | Value |
| --- | --- |
| Name | SealSkin |
| Callback URLs | `https://sealskin.example.com/api/auth/oidc/callback` |
| Public Client | Off |
| PKCE | On |

Pocket ID makes the client ID and shows the secret once.

```
SEALSKIN_OIDC_ISSUER=https://pocket.example.com
SEALSKIN_OIDC_CLIENT_ID=<the client ID Pocket ID made>
SEALSKIN_OIDC_CLIENT_SECRET=<the client secret>
SEALSKIN_OIDC_SCOPES=openid profile email groups
SEALSKIN_SSO_GROUPS_CLAIM=groups
SEALSKIN_SSO_ADMIN_GROUP=admins
```

The user is `preferred_username`, the Pocket ID user name, and `groups`
lists the names of the user's groups there (the name, not the friendly
name).

Pocket ID pairs with [Tinyauth](tinyauth.md) or
[oauth2-proxy](oauth2-proxy.md) for forward-auth, which then sign in at
Pocket ID and name the user to SealSkin in a header.
