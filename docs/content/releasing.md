---
title: Releasing
description: Versioning, release notes, the GitHub workflows, what each release carries, and the secrets they need.
---

## Workflows

| Workflow | Runs on | Does |
| --- | --- | --- |
| **CI** (`ci.yml`) | every push to `main` and every pull request | Lints (`ruff`) and tests (`pytest`) the server, builds the client and checks that every manifest carries the version from `VERSION`, builds and validates the wheel. |
| **Pre-release** (`prerelease.yml`) | every push to `main` | Builds everything, including the mobile apps, and publishes a GitHub pre-release named after the short commit SHA with all the artifacts attached. Nothing is uploaded to a store. |
| **Release** (`release.yml`) | every pushed tag | Refuses to run unless the tag matches `VERSION` and `release-notes/<VERSION>.md` exists; then builds everything, uploads the mobile apps to TestFlight and the Google Play internal track, and publishes a stable GitHub release with the notes as its body. |
| **Mobile** (`mobile.yml`) | called by the two above, or by hand from the Actions tab | Builds the signed IPA, APK and AAB. Uploads to the stores only when the `upload` input is set, which the Release workflow does and a manual run can. |
| **Docs** (`docs.yml`) | every push to `main` and every pull request touching `docs/` or the server | Builds this site, checks every link and anchor, verifies the Settings Reference is current, and (on `main`) deploys it to GitHub Pages. |

## Cutting a release

1. Set the new version in `VERSION` (for example `0.4.0`).
2. Write `release-notes/0.4.0.md`. It becomes the body of the GitHub release,
   so it is user-facing: what changed, what to do when upgrading.
3. Regenerate the [Settings Reference](settings.md) if settings changed, and
   update the [documentation](development.md#this-documentation) for anything
   user-visible.
4. Merge to `main` and let CI and the pre-release run. The pre-release is the
   release candidate: its extension zips, APK and IPA are what you test.
5. Tag the commit with the version and push the tag:

   ```bash
   git tag 0.4.0
   git push origin 0.4.0
   ```

The Release workflow checks the tag against `VERSION` and the notes file
first, so a mismatch fails before anything is built or uploaded.

### What a release carries

| Asset | Built by |
| --- | --- |
| `sealskin-chrome-v<VERSION>.zip`, `sealskin-firefox-v<VERSION>.zip` | `browser_extension/build.sh` |
| `sealskin-v<VERSION>.apk`, `sealskin-v<VERSION>.ipa` | `mobile/build.sh` and the iOS job |
| `sealskin_server-<VERSION>-py3-none-any.whl` | `python -m build --wheel server/` with the UI copied into the package |
| `sealskin-ui-v<VERSION>.tar.gz` | `tar` of `client/dist/ui` |

### Stores

* **Chrome Web Store and Firefox Add-ons** submissions are manual: upload the
  zips from the release. A store release is only needed when the shell
  itself changed (manifest, bridge protocol, background script), because the
  UI ships with the server.
* **TestFlight and Google Play** receive every stable release automatically
  from the Mobile workflow, and any manual run with `upload` ticked.
  Promotion from TestFlight and from the internal track to production is
  done in App Store Connect and the Play Console.

The store build number is the commit count on the branch plus an offset, so
it only ever goes up across workflows; the iOS marketing version is
`2.<build>` and is deliberately independent of `VERSION`. Both are derived in
the Mobile workflow's first job.

## Secrets

The Mobile workflow needs these repository secrets:

| Secret | Used for |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | Signing the APK and AAB. Without them `build.sh` produces a debug-signed APK. |
| `PLAY_SERVICE_ACCOUNT_JSON` | Uploading the AAB to the Google Play internal track. |
| `IOS_DIST_CERT_P12`, `IOS_DIST_CERT_PASSWORD`, `IOS_PROVISIONING_PROFILE` | The Apple Distribution certificate and the App Store provisioning profile for the bundle id. The workflow verifies that the profile was issued against that certificate. |
| `APPSTORE_KEY_ID`, `APPSTORE_ISSUER_ID`, `APPSTORE_PRIVATE_KEY` | The App Store Connect API key used by `altool` to upload the IPA. |

The Docs workflow needs GitHub Pages enabled with **GitHub Actions** as the
source; it uses the built-in `GITHUB_TOKEN`. CI, Pre-release and Release need
nothing beyond `contents: write` for creating releases.

## Release notes

`release-notes/<VERSION>.md` is required for a stable release and is written
by hand. Keep the tone of the existing notes: a short lead on what the
release means for someone running SealSkin, then **Highlights**, per-component
sections where useful, and an **Upgrading** section whenever an operator or
a user has to do something.
