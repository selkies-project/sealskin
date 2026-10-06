---
title: Nginx Proxy Manager
description: The two proxy hosts SealSkin needs in Nginx Proxy Manager, one for the web app and a wildcard one for the session names.
---

Run with Nginx Proxy Manager 2.16. Read
[Behind a reverse proxy](index.md) first for the settings on the SealSkin
side.

## Certificate

Under **SSL Certificates**, add a Let's Encrypt certificate for
`example.com` and `*.example.com` with **Use a DNS Challenge**, or upload a
wildcard certificate as a custom one.

## Two proxy hosts

| | Web app | Sessions |
| --- | --- | --- |
| Domain Names | `sealskin.example.com` | `*.example.com` |
| Scheme | `https` | `https` |
| Forward Hostname / IP | `sealskin` | `sealskin` |
| Forward Port | `8443` | `8443` |
| Websockets Support | on | on |
| Block Common Exploits | off | off |
| SSL Certificate | the wildcard certificate | the wildcard certificate |
| Force SSL | on | on |

Nginx Proxy Manager does not verify the certificate of an `https` upstream,
so the self-signed one on the session port is fine. For the
[plain HTTP listener](index.md#the-plain-http-listener), set
`SEALSKIN_HTTP_PORT=8080` on SealSkin and use scheme `http` and port `8080`
in both hosts.

The wildcard host takes every name under the domain that no other proxy
host names, so your other hosts keep working: nginx prefers an exact name.
A name that is neither a session's nor another host's reaches SealSkin too,
which answers there as it does on its own name.

The hosts were run with **Block Common Exploits** off.

`SEALSKIN_TRUSTED_PROXIES` is the address of the Nginx Proxy Manager
container on the network it shares with SealSkin.

## Signing users in

To sign users in at SealSkin itself over OpenID Connect or SAML, with any
provider of this section, the proxy hosts above need nothing more.

### Forward-auth

Nginx Proxy Manager has no forward-auth switch; the web app host's
**Advanced** tab takes the nginx configuration for one. This is
[Tinyauth](tinyauth.md)'s, with Tinyauth itself behind a third proxy host,
`tinyauth.example.com` to `http://tinyauth:3000`:

```nginx
location / {
    auth_request /tinyauth;
    error_page 401 = @tinyauth_login;
    auth_request_set $tinyauth_user $upstream_http_remote_user;
    auth_request_set $tinyauth_groups $upstream_http_remote_groups;
    proxy_set_header Remote-User $tinyauth_user;
    proxy_set_header Remote-Groups $tinyauth_groups;

    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $http_connection;
    proxy_http_version 1.1;
    include conf.d/include/proxy.conf;
}
location /tinyauth {
    internal;
    proxy_pass http://tinyauth:3000/api/auth/nginx;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Uri $request_uri;
}
location @tinyauth_login {
    return 302 https://tinyauth.example.com/login?redirect_uri=$scheme://$http_host$request_uri;
}
```

A `location /` of your own replaces the one Nginx Proxy Manager writes,
which is why it repeats the WebSocket lines and the include. The two
`proxy_set_header Remote-` lines keep a visitor's own headers out. Leave the
wildcard host for the sessions as it is, and set the sign-in headers on
SealSkin as [A proxy that signs users in](sign-in.md) describes.
