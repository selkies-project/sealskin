---
title: Authelia
description: Signing in to SealSkin with Authelia, over OpenID Connect or through the proxy's forward-auth, with the rule for public share links.
---

Run with Authelia 4.39. Authelia sits behind the same proxy at a name of its
own, `auth.example.com` here.

## OpenID Connect

SealSkin sends the browser to Authelia and takes the user from Authelia's
signed answer. The proxy does nothing but terminate TLS, so the key-file
clients and public share links keep working.

A client in Authelia's `configuration.yml`:

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: sealskin
        client_name: SealSkin
        client_secret: '$pbkdf2-sha512$...'   # authelia crypto hash generate pbkdf2 --variant sha512
        public: false
        authorization_policy: one_factor
        redirect_uris:
          - https://sealskin.example.com/api/auth/oidc/callback
        scopes: [openid, profile, groups, email, offline_access]
        grant_types: [authorization_code, refresh_token]
        response_types: [code]
        token_endpoint_auth_method: client_secret_basic
        require_pkce: true
        pkce_challenge_method: S256
```

On SealSkin, in the environment or in the dashboard under **Sign In**:

```
SEALSKIN_OIDC_ISSUER=https://auth.example.com
SEALSKIN_OIDC_CLIENT_ID=sealskin
SEALSKIN_OIDC_CLIENT_SECRET=<the secret, not its hash>
SEALSKIN_OIDC_SCOPES=openid profile groups email offline_access
SEALSKIN_SSO_GROUPS_CLAIM=groups
SEALSKIN_SSO_ADMIN_GROUP=admins
```

* The user is `preferred_username`, the Authelia user name. Authelia keeps
  it and `groups` out of the ID token unless a claims policy puts them
  there, and SealSkin reads them from Authelia's UserInfo endpoint instead,
  so no claims policy is needed.
* `offline_access` gets SealSkin a refresh token. With it SealSkin asks
  Authelia about the sign-in every minute while it is in use, so a user
  removed or moved between groups there is here within the minute. Without
  it the sign-in ends when its ID token expires.
* Authelia announces no logout to its clients. Signing out of SealSkin ends
  the SealSkin sign-in; the Authelia session is Authelia's to end.
* SealSkin reaches the issuer URL itself, to read its metadata and exchange
  the code, so the name has to resolve from inside the SealSkin container.

## Forward-auth

The proxy asks Authelia about every request and SealSkin takes the user from
the `Remote-User` header; see [A proxy that signs users in](sign-in.md) for
the SealSkin side and each proxy's page for where the middleware goes.

```
SEALSKIN_PROXY_AUTH_USER_HEADER=Remote-User
SEALSKIN_PROXY_AUTH_GROUPS_HEADER=Remote-Groups
SEALSKIN_PROXY_AUTH_LOGOUT_URL=https://auth.example.com/logout
SEALSKIN_SSO_ADMIN_GROUP=admins
```

Authelia's session cookie is set for the domain, so it covers the web app's
name and, where they are guarded, the session names:

```yaml
session:
  cookies:
    - domain: example.com
      authelia_url: https://auth.example.com
```

To let public share links through without a sign-in:

```yaml
access_control:
  default_policy: one_factor
  rules:
    - domain: sealskin.example.com
      resources: ['^/public/']
      policy: bypass
```

A request that passes on a `bypass` rule reaches SealSkin with no
`Remote-User`, whatever the visitor sent.
