---
title: SWAG
description: SealSkin behind LinuxServer's SWAG, with the proxy configuration for the web app and the session names, and its Authelia and Authentik includes.
---

Run with SWAG 5.8.0 (nginx 1.30). Read [Behind a reverse proxy](index.md)
first for the settings on the SealSkin side.

The session names, and the wildcard certificate that covers them, are for
[session isolation](index.md#session-isolation); without it the web app's
name is all SealSkin needs, and the rule for the session names is harmless
and may be left out.

## Certificate

With session isolation, sessions need a wildcard certificate, which SWAG
gets with DNS validation:

```yaml
  swag:
    image: lscr.io/linuxserver/swag:latest
    environment:
      - URL=example.com
      - SUBDOMAINS=wildcard
      - VALIDATION=dns
      - DNSPLUGIN=cloudflare      # your DNS provider; its credentials go in /config/dns-conf
```

With Duck DNS a wildcard certificate covers the subdomains only, so the web
app lives on one too: `sealskin.you.duckdns.org`, with sessions on
`<session id>.you.duckdns.org` and `SEALSKIN_SESSION_DOMAIN=you.duckdns.org`.

## The proxy configuration

Save as `/config/nginx/proxy-confs/sealskin.subdomain.conf`. The container
is named `sealskin` and has a DNS name `sealskin.<your domain>`.

```nginx
## Version 2026/10/06
# make sure that your sealskin container is named sealskin
# make sure that your dns has a cname set for sealskin, and a wildcard for the session names
# sealskin opens every session of its web app on <session id>.<session domain>: the
# certificate has to be a wildcard one, and the second server block below takes those names
# set SEALSKIN_PUBLIC_URL, SEALSKIN_SESSION_DOMAIN and SEALSKIN_TRUSTED_PROXIES on sealskin

server {
    listen 443 ssl;
    listen [::]:443 ssl;

    server_name sealskin.*;

    include /config/nginx/ssl.conf;

    client_max_body_size 0;

    # enable for Authelia (requires authelia-location.conf in the location block)
    #include /config/nginx/authelia-server.conf;

    # enable for Authentik (requires authentik-location.conf in the location block)
    #include /config/nginx/authentik-server.conf;

    location / {
        # enable for Authelia (requires authelia-server.conf in the server block)
        #include /config/nginx/authelia-location.conf;

        # enable for Authentik (requires authentik-server.conf in the server block)
        #include /config/nginx/authentik-location.conf;

        include /config/nginx/proxy.conf;
        include /config/nginx/resolver.conf;
        set $upstream_app sealskin;
        set $upstream_port 8443;
        set $upstream_proto https;
        proxy_pass $upstream_proto://$upstream_app:$upstream_port;

        proxy_buffering off;
        proxy_request_buffering off;
    }
}

# the sessions' own names: anything that starts with a session id
server {
    listen 443 ssl;
    listen [::]:443 ssl;

    server_name "~^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.";

    include /config/nginx/ssl.conf;

    client_max_body_size 0;

    location / {
        include /config/nginx/proxy.conf;
        include /config/nginx/resolver.conf;
        set $upstream_app sealskin;
        set $upstream_port 8443;
        set $upstream_proto https;
        proxy_pass $upstream_proto://$upstream_app:$upstream_port;

        proxy_buffering off;
        proxy_request_buffering off;
    }
}
```

The regular expression has to stay in quotes: nginx reads a bare `{` as the
start of a block. It takes the names that start with a session id and
nothing else, so every other `*.subdomain.conf` keeps working beside it.

For the [plain HTTP listener](index.md#the-plain-http-listener), set
`SEALSKIN_HTTP_PORT=8080` on SealSkin and, in both blocks:

```nginx
        set $upstream_port 8080;
        set $upstream_proto http;
```

`proxy.conf` sets `Host`, the WebSocket headers, and `X-Forwarded-Proto` as
SealSkin wants them. Its `proxy_read_timeout` of 240 seconds is enough: a
stream is never that quiet.

## With Authelia or Authentik in front

To sign users in at SealSkin itself over OpenID Connect or SAML, leave the
configuration above as it is and follow [Authelia](authelia.md) or
[Authentik](authentik.md).

To have SWAG ask for the sign-in, remove the `#` from the two lines of your
provider **in the first server block**, and set the sign-in headers on
SealSkin as [A proxy that signs users in](sign-in.md) describes. With the
two lines left commented, nginx passes a visitor's own `Remote-User`
through, which SealSkin detects and refuses.

The second server block needs no include. To guard the session names too,
add the same two lines there and set `SEALSKIN_SESSION_DOMAIN`.

Authelia's and Authentik's own proxy configurations ship with SWAG as
`authelia.subdomain.conf.sample` and `authentik.subdomain.conf.sample`.

## On a port other than 443

Published on another port, as `8443`, SWAG's `proxy.conf` sends the
provider a `Host` without it (see
[A port other than 443](index.md#a-port-other-than-443)); SealSkin does not
mind, Authelia and Authentik do. Copy `proxy.conf` to
`/config/nginx/proxy-port.conf`, change its two lines to
`proxy_set_header Host $http_host;` and
`proxy_set_header X-Forwarded-Host $http_host;`, and include the copy in
the provider's own `*.subdomain.conf` in place of `proxy.conf`. Leave
`proxy.conf` itself alone, so SWAG's updates keep applying to it.
