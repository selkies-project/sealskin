# Working on this repository

SealSkin is developed beside [selkies](https://github.com/selkies-project/selkies), which streams the
desktops it orchestrates, and follows the same working conventions; this file holds them and the
cross-cutting rules no single module reveals. Mechanism lives in the docstring of the module that
implements it and in the [development page](docs/content/development.md), never here.

## Layout

`VERSION` at the root is the one version of the server, the served UI, the browser extension, and
the mobile shells. `server/` is the FastAPI application with its tests, Caddy template, and wheel
packaging; `client/` the plain-JavaScript UI and the esbuild pipeline that also packages the
extension and mobile shells; `browser_extension/` the manifests and zip script; `mobile/` the
Capacitor project; `kubernetes/` the manifest that installs the server in a namespace; `docs/` the Fumadocs site over the pages under `docs/content`; `release-notes/`
one Markdown file per stable release. `docs/AGENTS.md` is the block `next dev` writes for the site's
own Next.js version, not a copy of this file.

## Comments and documentation

Google-style docstrings on every module, class, and public function, with the types on the
signature; Ruff enforces the `D` rules with the Google convention. The Server Reference is rendered
from those docstrings as Markdown, so a docblock describes the tree as it is now, names no issue or
task number, narrates no past revision, and keeps anything shaped like `<name>` inside backticks.
Comments are terse and say why a line is the way it is, never what it does or what it replaced.
Everything is written in American English. The prose under `docs/content` follows the same rule.

## Invariants

- Settings are declared once, in `SETTING_DEFINITIONS`; a new setting is an entry there and a
  regenerated `docs/content/settings.md` (`npm run generate:settings` in `docs/`), which CI checks.
- All backend access goes through the provider `get_provider` returns (Docker's also through
  `docker_utils`), all YAML through `persistence`, and every launch through `build_launch_spec`.
- The collaboration and token tables (`server/app/collaboration.py`) drive Selkies' secure mode as
  its documentation describes it, and are a reference implementation of it: a change there is
  measured against a Selkies session, not reasoned about.
- `VERSION` is read by the client build, the packaging scripts, `server/app/version.py`, and the
  wheel build; a release tag is that version exactly, with no leading `v`, on a commit of `main`,
  with `release-notes/<version>.md` present, or the release workflow refuses it.

## Testing

Run what CI runs before reporting a change: `ruff check server` and `pytest server/tests`, the
client build (`npm ci && npm run build` in `client/`), the version consistency check, and
`python3 docs/scripts/generate-settings-doc.py --check` for the docs. A change to the served UI or
a shell is tried in the extension or app it ships in, not only in the build; a change to a session
is tried against a running Selkies container, which the development page shows how to start.
Validate in a sandbox, never in a session someone is using.

## Landing a change

One commit per concern under a one-line `type: Sentence` subject, the contributor's own identity
on it, no generated bundles, and the suites run named in the pull request. Pushes to `main` alone
publish anything: the pre-release, the site, the mobile store uploads, and the release; every other
ref builds self-contained. An issue is closed by a maintainer, never by you.
