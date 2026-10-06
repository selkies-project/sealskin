---
title: Behind a reverse proxy
description: What SealSkin needs from a proxy in front of it (names, a wildcard certificate, WebSockets), the settings that tell the server about it, and the two ways the proxy reaches it.
---

SealSkin's own proxy, the Caddy inside the container, terminates TLS and
routes every session. A reverse proxy in front of it can take over the
certificate, share port 443 with other services, and sign users in with the
identity provider those services already use. This section is how to set
that up, with configurations that were run against the versions named on
each page:

| Page | What it covers |
| --- | --- |
| [A proxy that signs users in](sign-in.md) | Forward-auth: the proxy's header names the user, and how SealSkin checks that the proxy can be believed. |
| [SWAG](swag.md) | LinuxServer's nginx and certbot container. |
| [Traefik](traefik.md) | Docker labels for routers, the wildcard rule, and the forward-auth middleware. |
| [Nginx Proxy Manager](nginx-proxy-manager.md) | The two proxy hosts to create. |
| [Caddy](caddy.md) | A Caddy of your own in front. |
| [Authelia](authelia.md) | OpenID Connect client, forward-auth rules, signing out. |
| [Authentik](authentik.md) | OpenID Connect, SAML, and the proxy provider. |
| [Keycloak](keycloak.md) | OpenID Connect and SAML clients, and the mappers for groups. |
| [Pocket ID](pocket-id.md) | OpenID Connect client. |
| [Tinyauth](tinyauth.md) | Forward-auth, and its OpenID Connect provider. |
| [oauth2-proxy](oauth2-proxy.md) | Forward-auth in front of any OpenID Connect provider. |
| [Troubleshooting](troubleshooting.md) | What each failure looks like and what causes it. |

The [web sign-in](../signin.md) is what a proxy serves well. The key-file
clients (browser extension, mobile app) work through a proxy that only
terminates TLS, and not through one that asks for a sign-in of its own.

## In short

1. Point two DNS names at the proxy: the web app's, `sealskin.example.com`,
   and a wildcard for the sessions, `*.example.com`.
2. Get a wildcard certificate for the proxy, which takes a DNS challenge.
3. Give the proxy the rule from its page: both kinds of names to SealSkin,
   with WebSockets.
4. Set `SEALSKIN_PUBLIC_URL`, `SEALSKIN_SESSION_DOMAIN`, and
   `SEALSKIN_TRUSTED_PROXIES` on SealSkin, and take its published ports
   away.
5. Open the web app, sign in with the
   [root token](../signin.md#the-root-administrator), and look at
   **Settings → Sign In → Reaching This Server**. It shows the address the
   server believes it has, whether a session name answers this browser, the
   address the request came from and whether that is a trusted proxy, and
   your own address as the server sees it. Each line that is wrong names
   the setting to change.
6. Choose how users sign in, in the cards below it on the same page: an
   identity provider over OpenID Connect or SAML, or the proxy's own
   sign-in. Each card has a list of providers that fills in what it can.

## What the proxy has to do

* **Serve two kinds of names.** The web app lives on one name,
  `sealskin.example.com`. Every session of a web sign-in opens on a name of
  its own, `<session id>.<session domain>`, so that nothing a session's page
  runs can use the web app's cookie. The proxy sends both to SealSkin, which
  takes DNS for `*.<session domain>` and a certificate that covers it: a
  wildcard certificate, which a certificate authority issues over a DNS
  challenge. A session id is 36 characters of hexadecimal and dashes, so a
  rule can match those names alone and leave the rest of the domain to
  other services, which every page here does.
* **Pass the request as the browser sent it.** The `Host` header, the path,
  `Origin`, the `Sec-Fetch-*` headers, and cookies go through unchanged,
  which is what each proxy does unless told otherwise. SealSkin cannot be
  served under a path of another site (`example.com/sealskin/`).
* **Pass WebSockets.** A session is one long-lived WebSocket, and a
  collaboration room is another.
* **Leave bodies alone.** No limit on the request size (uploads go up in
  chunks, and files are sent to sessions), and no response buffering.

## Telling SealSkin

```yaml
services:
  sealskin:
    image: lscr.io/linuxserver/sealskin:latest
    container_name: sealskin
    environment:
      - HOST_URL=sealskin.example.com:443
      - SEALSKIN_PUBLIC_URL=https://sealskin.example.com
      - SEALSKIN_SESSION_DOMAIN=example.com
      - SEALSKIN_TRUSTED_PROXIES=172.20.0.2
    volumes:
      - ./config:/config
      - ./storage:/storage
      - /var/run/docker.sock:/var/run/docker.sock
    # No ports: the proxy reaches the container on the Docker network they share.
```

| Setting | Meaning |
| --- | --- |
| `SEALSKIN_PUBLIC_URL` | The web app's address as browsers type it, with the port when it is not 443. Identity provider redirects are built from it. |
| `SEALSKIN_SESSION_DOMAIN` | The domain sessions open under: `example.com` gives `<session id>.example.com`. Empty leaves the web app to try its own name and its parent's. Name it whenever the proxy signs users in on the session names too. |
| `SEALSKIN_TRUSTED_PROXIES` | The proxy's address on the network it reaches SealSkin over. From that address the server takes the browser's address out of `X-Forwarded-For`, serves the [plain HTTP listener](#the-plain-http-listener), and believes a [sign-in header](sign-in.md). |

Give the proxy a fixed address and list that address alone. Sessions are
containers on the same network as SealSkin, so a whole network in
`SEALSKIN_TRUSTED_PROXIES` would trust every desktop a user runs on it. A
sign-in header is refused from a session's address whatever is listed, but
the forwarded client address is not.

```yaml
  swag:
    networks:
      default:
        ipv4_address: 172.20.0.2

networks:
  default:
    ipam:
      config:
        - subnet: 172.20.0.0/24
          ip_range: 172.20.0.128/25   # other containers start here, clear of the proxy
```

Do not publish SealSkin's ports on the host once a proxy is in front: what
reaches them directly did not pass the proxy.

## How the proxy reaches SealSkin

Either of two ways.

**The session port over HTTPS**, `https://sealskin:8443`. It works with no
further setting. The certificate there is the self-signed one unless you
placed your own, so the proxy must not verify it; SWAG and Nginx Proxy
Manager do not, Traefik and Caddy have a switch.

### The plain HTTP listener

**A plain HTTP port**, for a proxy on the same host or a network you trust:

```
SEALSKIN_HTTP_PORT=8080
```

The listener serves what the session port does and is closed to everything
but a careful proxy:

* It answers the addresses in `SEALSKIN_TRUSTED_PROXIES` alone; anything else
  gets a 403 that names the address it came from. With no trusted proxy set
  it does not open.
* It serves a request only when the proxy says, in `X-Forwarded-Proto`, that
  the browser sent it over HTTPS. A request the browser sent over plain HTTP
  is redirected to `SEALSKIN_PUBLIC_URL`, and one the proxy says nothing
  about is refused, so a proxy that would serve SealSkin without TLS serves
  nothing.

The proxies in this section send `X-Forwarded-Proto` unasked.

## A port other than 443

Where the proxy is published on another port, as `8443` behind a router that
keeps 443, put the port in `HOST_URL` and `SEALSKIN_PUBLIC_URL`
(`https://sealskin.example.com:8443`). The web app opens sessions on the
port it was loaded from, and SealSkin's own redirects are relative, so it
does not need the port in `Host`. An identity provider behind the same proxy
may: nginx's `$host`, which SWAG's `proxy.conf` and Nginx Proxy Manager
send, carries no port, and Authelia and Authentik then answer for a URL they
do not know. Send them `$http_host`.

## Signing users in

| | The proxy only terminates TLS | The proxy signs users in |
| --- | --- | --- |
| Who asks for the password | SealSkin sends the browser to the provider over [OpenID Connect or SAML](../signin.md#openid-connect) | The proxy, before a request reaches SealSkin |
| SealSkin learns the user from | The provider's signed answer | A header the proxy adds |
| Key-file clients | Work | Are turned away by the proxy |
| Public share links, room guests | Work | Need rules at the proxy |
| Root token | Works | Works, once past the proxy's sign-in, at `/ui/#root` |

The first column is the one to prefer: every provider in this section
speaks OpenID Connect, a user signed in there goes straight through, and
nothing rests on how the proxy is configured. The second is for where every site
behind the proxy is already guarded that way, and is described under
[A proxy that signs users in](sign-in.md).
