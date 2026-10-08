---
title: Caddy
description: A Caddy of your own in front of SealSkin, with one site for the web app and the session names, and Authelia's forward_auth.
---

Run with Caddy 2.11. Read [Behind a reverse proxy](index.md) first for the
settings on the SealSkin side. This is a Caddy you run in front; the one
inside the SealSkin container is not configured by hand.

The session names, and the wildcard certificate that covers them, are for
[session isolation](index.md#session-isolation); without it the web app's
name is all SealSkin needs, and the rule for the session names is harmless
and may be left out.

## Caddyfile

One site takes the web app's name and the wildcard, and sends SealSkin the
web app and the names that start with a session id. A wildcard certificate
takes a Caddy build with your DNS provider's module.

```caddyfile
sealskin.example.com, *.example.com {
	tls {
		dns cloudflare {env.CLOUDFLARE_API_TOKEN}
	}

	@app host sealskin.example.com
	@session header_regexp Host ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.

	handle @app {
		reverse_proxy sealskin:8080
	}
	handle @session {
		reverse_proxy sealskin:8080
	}
	handle {
		respond 404
	}
}
```

This uses the [plain HTTP listener](index.md#the-plain-http-listener), so
SealSkin has `SEALSKIN_HTTP_PORT=8080` and Caddy's address in
`SEALSKIN_TRUSTED_PROXIES`. `reverse_proxy` passes `Host` and WebSockets and
sets `X-Forwarded-Proto` unasked.

For the session port over HTTPS instead:

```caddyfile
		reverse_proxy https://sealskin:8443 {
			transport http {
				tls_insecure_skip_verify
			}
		}
```

## Forward-auth

To sign users in at SealSkin itself over OpenID Connect or SAML, the
Caddyfile above is all there is.

To have Caddy ask for the sign-in, here with Authelia, put `forward_auth`
before the web app's `reverse_proxy`:

```caddyfile
	handle @app {
		forward_auth authelia:9091 {
			uri /api/authz/forward-auth
			copy_headers Remote-User Remote-Groups Remote-Email Remote-Name
		}
		reverse_proxy sealskin:8080
	}
```

Then set the sign-in headers on SealSkin as
[A proxy that signs users in](sign-in.md) describes. `copy_headers` removes
what a visitor sent under those names before it copies the provider's
answer. A site without `forward_auth` passes a visitor's own `Remote-User`
through, which SealSkin detects and refuses.
