---
title: Traefik
description: SealSkin behind Traefik 3 with Docker labels, a router for the web app and one for the session names, either upstream, and Authelia's forward-auth middleware.
---

Run with Traefik 3.7. Read [Behind a reverse proxy](index.md) first for the
settings on the SealSkin side.

The session names, and the wildcard certificate that covers them, are for
[session isolation](index.md#session-isolation); without it the web app's
name is all SealSkin needs, and the rule for the session names is harmless
and may be left out.

## Labels

Two routers share one service: the web app's name, and every name that
starts with a session id. The certificate has to cover both, so the
resolver uses a DNS challenge for `example.com` and `*.example.com`.

```yaml
services:
  traefik:
    image: traefik:v3
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.websecure.address=:443
      - --entrypoints.websecure.http.tls.certresolver=letsencrypt
      - --entrypoints.websecure.http.tls.domains[0].main=example.com
      - --entrypoints.websecure.http.tls.domains[0].sans=*.example.com
      - --certificatesresolvers.letsencrypt.acme.dnschallenge.provider=cloudflare
      - --certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json
    ports:
      - 443:443
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt
    networks:
      default:
        ipv4_address: 172.20.0.2

  sealskin:
    image: lscr.io/linuxserver/sealskin:latest
    container_name: sealskin
    environment:
      - HOST_URL=sealskin.example.com:443
      - SEALSKIN_PUBLIC_URL=https://sealskin.example.com
      - SEALSKIN_SESSION_DOMAIN=example.com
      - SEALSKIN_TRUSTED_PROXIES=172.20.0.2
      - SEALSKIN_HTTP_PORT=8080
    volumes:
      - ./config:/config
      - ./storage:/storage
      - /var/run/docker.sock:/var/run/docker.sock
    labels:
      - traefik.enable=true
      - traefik.http.routers.sealskin.rule=Host(`sealskin.example.com`)
      - traefik.http.routers.sealskin.entrypoints=websecure
      - traefik.http.routers.sealskin.service=sealskin
      - traefik.http.routers.sealskin-sessions.rule=HostRegexp(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.`)
      - traefik.http.routers.sealskin-sessions.entrypoints=websecure
      - traefik.http.routers.sealskin-sessions.service=sealskin
      - traefik.http.services.sealskin.loadbalancer.server.port=8080

networks:
  default:
    ipam:
      config:
        - subnet: 172.20.0.0/24
          ip_range: 172.20.0.128/25
```

This uses the [plain HTTP listener](index.md#the-plain-http-listener).
Traefik names a WebSocket's scheme `wss` in `X-Forwarded-Proto`, which the
listener takes for HTTPS.

## The session port instead

To reach `8443` over HTTPS, drop `SEALSKIN_HTTP_PORT` and tell Traefik not to
verify the self-signed certificate there:

```yaml
      - traefik.http.services.sealskin.loadbalancer.server.port=8443
      - traefik.http.services.sealskin.loadbalancer.server.scheme=https
```

```yaml
    command:
      - --serverstransport.insecureskipverify=true
```

That switch is for every service of this Traefik. To keep it to SealSkin,
define a `serversTransport` in the file provider and name it in
`traefik.http.services.sealskin.loadbalancer.serverstransport`.

## Forward-auth

To sign users in at SealSkin itself over OpenID Connect or SAML, the labels
above are all there is; follow [Authelia](authelia.md) or
[Authentik](authentik.md).

To have Traefik ask for the sign-in, define the provider's middleware once,
here Authelia's, on the Authelia container:

```yaml
      - traefik.http.middlewares.authelia.forwardauth.address=http://authelia:9091/api/authz/forward-auth
      - traefik.http.middlewares.authelia.forwardauth.trustForwardHeader=true
      - traefik.http.middlewares.authelia.forwardauth.authResponseHeaders=Remote-User,Remote-Groups,Remote-Email,Remote-Name
```

and put it on the web app's router:

```yaml
      - traefik.http.routers.sealskin.middlewares=authelia@docker
```

Then set the sign-in headers on SealSkin as
[A proxy that signs users in](sign-in.md) describes. Traefik replaces the
headers listed in `authResponseHeaders` with the provider's answer, and
removes the ones the answer leaves out. A router without the middleware
passes a visitor's own `Remote-User` through, which SealSkin detects and
refuses.

To guard the session names too, add the same middleware to
`sealskin-sessions` and set `SEALSKIN_SESSION_DOMAIN`.
