---
title: oauth2-proxy
description: oauth2-proxy as the forward-auth in front of SealSkin, with the nginx configuration that asks it and the headers SealSkin reads.
---

Run with oauth2-proxy 7.15 behind SWAG, signing in at Keycloak. oauth2-proxy
turns any OpenID Connect provider into a forward-auth; where the provider is
yours to configure, signing in at SealSkin itself over
[OpenID Connect](../signin.md#openid-connect) does the same with one part
fewer.

## oauth2-proxy

Its callback lives under SealSkin's own name, `/oauth2/`, which the proxy
routes to it.

```yaml
  oauth2-proxy:
    image: quay.io/oauth2-proxy/oauth2-proxy:latest
    container_name: oauth2-proxy
    environment:
      - OAUTH2_PROXY_PROVIDER=keycloak-oidc
      - OAUTH2_PROXY_OIDC_ISSUER_URL=https://keycloak.example.com/realms/home
      - OAUTH2_PROXY_CLIENT_ID=oauth2-proxy
      - OAUTH2_PROXY_CLIENT_SECRET=<the client's secret>
      - OAUTH2_PROXY_COOKIE_SECRET=<openssl rand -base64 32 | tr -- '+/' '-_'>
      - OAUTH2_PROXY_REDIRECT_URL=https://sealskin.example.com/oauth2/callback
      - OAUTH2_PROXY_EMAIL_DOMAINS=*
      - OAUTH2_PROXY_HTTP_ADDRESS=0.0.0.0:4180
      - OAUTH2_PROXY_REVERSE_PROXY=true
      - OAUTH2_PROXY_SET_XAUTHREQUEST=true
      - OAUTH2_PROXY_UPSTREAMS=static://202
      - OAUTH2_PROXY_SKIP_PROVIDER_BUTTON=true
      - OAUTH2_PROXY_CODE_CHALLENGE_METHOD=S256
      - OAUTH2_PROXY_OIDC_GROUPS_CLAIM=groups
```

`OAUTH2_PROXY_SET_XAUTHREQUEST` is what makes it answer with the headers
that name the user. Register `https://sealskin.example.com/oauth2/callback`
as the client's redirect URI at the provider.

## nginx

In the first server block of the [SWAG](swag.md) configuration, before
`location /`:

```nginx
    # oauth2-proxy: its own pages, and the question nginx asks it about every request
    location /oauth2/ {
        include /config/nginx/resolver.conf;
        set $upstream_oauth2 oauth2-proxy;
        proxy_pass http://$upstream_oauth2:4180;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location = /oauth2/auth {
        internal;
        include /config/nginx/resolver.conf;
        set $upstream_oauth2 oauth2-proxy;
        proxy_pass http://$upstream_oauth2:4180;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Uri $request_uri;
        proxy_set_header Content-Length "";
        proxy_pass_request_body off;
    }
    location @oauth2_signin {
        internal;
        set_escape_uri $oauth2_target $request_uri;
        return 302 $scheme://$http_host/oauth2/start?rd=$oauth2_target;
    }
```

and at the top of `location /`:

```nginx
        auth_request /oauth2/auth;
        error_page 401 = @oauth2_signin;
        auth_request_set $oauth2_user $upstream_http_x_auth_request_preferred_username;
        auth_request_set $oauth2_groups $upstream_http_x_auth_request_groups;
        proxy_set_header X-Auth-Request-Preferred-Username $oauth2_user;
        proxy_set_header X-Auth-Request-Groups $oauth2_groups;
        auth_request_set $oauth2_cookie $upstream_http_set_cookie;
        add_header Set-Cookie $oauth2_cookie;
```

The two `proxy_set_header` lines are what keep a visitor's own headers out:
nginx sends SealSkin oauth2-proxy's answer under those names, or nothing.

## SealSkin

```
SEALSKIN_PROXY_AUTH_USER_HEADER=X-Auth-Request-Preferred-Username
SEALSKIN_PROXY_AUTH_GROUPS_HEADER=X-Auth-Request-Groups
SEALSKIN_PROXY_AUTH_LOGOUT_URL=https://sealskin.example.com/oauth2/sign_out
SEALSKIN_SSO_ADMIN_GROUP=admins
```

Use the preferred user name. oauth2-proxy's `X-Auth-Request-User` is the
provider's subject, a UUID at Keycloak, and `X-Auth-Request-Email` is not a
SealSkin user name. See [A proxy that signs users in](sign-in.md) for the
rest of the SealSkin side.

`/oauth2/sign_out` ends oauth2-proxy's session. The provider's own session
lives on, so the next visit signs straight back in unless the URL carries on
to the provider's logout, as `?rd=` followed by its encoded end-session URL
does.
