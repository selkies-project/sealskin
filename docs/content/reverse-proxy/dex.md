---
title: Dex
description: Signing in to SealSkin with GitHub, or another upstream, through Dex over OpenID Connect, and placing Dex under the web app's own name.
---

Run with Dex 2.46. Dex has no users of its own: it signs them in at an
upstream, GitHub here, and issues OpenID Connect to SealSkin. It is one
container with a YAML file and an SQLite file, so it is the small way to put
a GitHub organization, a Google workspace, or an LDAP directory in front of
SealSkin where nothing else needs an identity provider.

## Where Dex lives

Dex needs an HTTPS address of its own. Behind a proxy it can sit at a name
of its own, `auth.example.com`, like the other providers here, or under a
path of the web app's name, which needs no second name or certificate. The
issuer carries the path, and Dex serves everything under it. With
[Caddy](caddy.md) in front:

```caddyfile
sealskin.example.com {
	handle /dex/* {
		reverse_proxy dex:5556
	}
	handle {
		reverse_proxy sealskin:8080
	}
}
```

## GitHub

Register an OAuth app under the organization or a member's account, with the
callback URL `https://sealskin.example.com/dex/callback`, the issuer followed
by `/callback`. An organization that restricts third-party access has to
approve the app, or GitHub hides its membership from Dex and nobody is let
in.

## Dex

`dex/config.yaml`:

```yaml
issuer: https://sealskin.example.com/dex

storage:
  type: sqlite3
  config:
    file: /var/dex/dex.db

web:
  http: 0.0.0.0:5556

oauth2:
  skipApprovalScreen: true

connectors:
  - type: github
    id: github
    name: GitHub
    config:
      clientID: $GITHUB_CLIENT_ID
      clientSecret: $GITHUB_CLIENT_SECRET
      redirectURI: https://sealskin.example.com/dex/callback
      orgs:
        - name: example-org
      teamNameField: slug
      useLoginAsID: true

staticClients:
  - id: sealskin
    name: SealSkin
    secretEnv: SEALSKIN_CLIENT_SECRET
    redirectURIs:
      - https://sealskin.example.com/api/auth/oidc/callback
```

```yaml
  dex:
    image: ghcr.io/dexidp/dex:v2.46.1
    container_name: dex
    command: ["dex", "serve", "/etc/dex/config.yaml"]
    environment:
      - GITHUB_CLIENT_ID=<the OAuth app's client ID>
      - GITHUB_CLIENT_SECRET=<its client secret>
      - SEALSKIN_CLIENT_SECRET=<a long random string>
    volumes:
      - ./dex/config.yaml:/etc/dex/config.yaml:ro
      - ./dex/data:/var/dex     # owned by uid 1001, which Dex runs as
```

* `orgs` is who may sign in: a member of any organization listed, or, where
  an entry names `teams`, of one of those teams. Dex asks GitHub for
  `read:org` to see them, which covers private memberships. This list is
  the whole of the rule: SealSkin shows nothing of it and never sees who
  Dex turned away, so the Sign In tab names the administrators' group alone
  (see [who a sign-in is](../signin.md#who-a-sign-in-is)).
* With one connector and no password database Dex sends the browser
  straight to GitHub; `skipApprovalScreen` drops its consent page.
* The SQLite file holds Dex's signing keys, codes, and refresh tokens. Dex's
  own documentation keeps it for small installations; `storage` takes
  Postgres or MySQL for a larger one.

## SealSkin

In the environment or in the dashboard under **Sign In**:

```
SEALSKIN_OIDC_ISSUER=https://sealskin.example.com/dex
SEALSKIN_OIDC_CLIENT_ID=sealskin
SEALSKIN_OIDC_CLIENT_SECRET=<the same string>
SEALSKIN_OIDC_SCOPES=openid profile email groups offline_access
SEALSKIN_SSO_GROUPS_CLAIM=groups
SEALSKIN_SSO_ADMIN_GROUP=example-org:admins
SEALSKIN_SSO_HOLD_NEW_USERS=false
```

* The user is `preferred_username`, the GitHub login. Dex puts `groups` in
  the ID token only when the `groups` scope is asked for, and
  `offline_access` gets a refresh token, with which SealSkin takes the
  user's teams as GitHub names them every minute and ends the sign-in of
  anyone GitHub no longer vouches for.
* `groups` lists the user's teams in the organizations of `orgs`, as
  `<organization>:<team slug>` with `teamNameField: slug` (the team's name
  without it). An entry with `teams` lists those teams alone.
* So an organization's teams map to SealSkin's groups by those names, and
  one team makes the administrators through `SEALSKIN_SSO_ADMIN_GROUP`. A
  member in no team arrives with no groups, which holds them for an
  administrator's approval; `SEALSKIN_SSO_HOLD_NEW_USERS=false` puts every
  member of the organization in `default` at once instead.

Signing out ends at SealSkin: Dex keeps no session of its own and GitHub
remembers the app's authorization, so the next sign-in goes straight through
while the user is signed in to GitHub.
