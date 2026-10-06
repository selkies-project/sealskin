---
title: Tinyauth
description: Signing in to SealSkin with Tinyauth, as the proxy's forward-auth or over OpenID Connect.
---

Run with Tinyauth 5.0. Tinyauth sits behind the same proxy at a name of its
own, `tinyauth.example.com` here, and sets its cookie for the parent domain,
so it has to share that domain with SealSkin.

```yaml
  tinyauth:
    image: ghcr.io/steveiliop56/tinyauth:latest   # releases after 5.0.7 are at ghcr.io/tinyauthapp/tinyauth
    container_name: tinyauth
    environment:
      - TINYAUTH_APPURL=https://tinyauth.example.com
      - TINYAUTH_AUTH_USERS=alice:$$2a$$10$$...     # tinyauth user create --docker
      - TINYAUTH_AUTH_SECURECOOKIE=true
```

## Forward-auth

The proxy asks Tinyauth about every request and SealSkin takes the user
from the `Remote-User` header; see
[A proxy that signs users in](sign-in.md) for the SealSkin side.

```
SEALSKIN_PROXY_AUTH_USER_HEADER=Remote-User
SEALSKIN_PROXY_AUTH_GROUPS_HEADER=Remote-Groups
SEALSKIN_PROXY_AUTH_LOGOUT_URL=https://tinyauth.example.com/logout
```

* **SWAG** ships the two includes, `tinyauth-server.conf` and
  `tinyauth-location.conf`, and the portal's own
  `tinyauth.subdomain.conf.sample`. Add them to the first server block of the
  [SWAG](swag.md) configuration where the Authelia and Authentik lines are:

  ```nginx
      include /config/nginx/tinyauth-server.conf;

      location / {
          include /config/nginx/tinyauth-location.conf;
  ```

* **Nginx Proxy Manager** takes it in the web app host's Advanced tab, shown
  on [its page](nginx-proxy-manager.md#forward-auth).

Tinyauth's own users have no groups, so `Remote-Groups` is empty for them
and no group makes an administrator; groups come with its LDAP and OAuth
users. To appoint administrators, open
`https://sealskin.example.com/ui/#root`, sign in with the
[root token](../signin.md#the-root-administrator), and give the users the
administrator switch, their own or a group's.

## OpenID Connect

Tinyauth is an OpenID Connect provider too. A client is a set of variables,
`SEALSKIN` in their names being a label of your choosing:

```yaml
      - TINYAUTH_OIDC_CLIENTS_SEALSKIN_CLIENTID=sealskin
      - TINYAUTH_OIDC_CLIENTS_SEALSKIN_CLIENTSECRET=<a long random string>
      - TINYAUTH_OIDC_CLIENTS_SEALSKIN_NAME=SealSkin
      - TINYAUTH_OIDC_CLIENTS_SEALSKIN_TRUSTEDREDIRECTURIS=https://sealskin.example.com/api/auth/oidc/callback
```

On SealSkin, in the environment or in the dashboard under **Sign In**:

```
SEALSKIN_OIDC_ISSUER=https://tinyauth.example.com
SEALSKIN_OIDC_CLIENT_ID=sealskin
SEALSKIN_OIDC_CLIENT_SECRET=<the same string>
SEALSKIN_OIDC_SCOPES=openid profile email groups
```

The user is `preferred_username`, the Tinyauth user name. Point
`TINYAUTH_OIDC_PRIVATEKEYPATH` and `TINYAUTH_OIDC_PUBLICKEYPATH` at a
volume, so the key Tinyauth signs with outlives the container.
