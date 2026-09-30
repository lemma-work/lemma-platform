# API and SDK versioning

Every Lemma-owned component — the API, both SDKs, the CLI, the desktop app, the
stack tool and the packages they are built from — ships under one version
string. That is what a release is *called*, and it is checked rather than
trusted: `scripts/check_version_consistency.py` reads the version out of every
component and the release workflows run it against the tag before anything is
published.

While the platform is in beta the compatibility line is the SemVer major and
minor:

- A compatible API change bumps the patch.
- A breaking API change bumps the minor, for example `0.7.x` to `0.8.0`.
- After `1.0`, a breaking API change bumps the major.

## When the number moves

At release, not on the branch. Regeneration used to refuse a schema change that
did not also bump `API_VERSION`, so a number naming a release that had not
happened climbed once per pull request, and every schema-touching branch
conflicted with every other one on the same handful of lines.

Components are therefore free to disagree while work is in flight, and must
agree at the moment something is published. A release is one change that sets
every component to the new version, regenerates what embeds it — the bundled
specs, both generated clients, the browser bundle, the lockfiles — and adds the
changelog entry and the release notes.

## Release notes

The GitHub Release page is where people land to download the desktop app, so
its description is written for them, not generated. Add
`docs/release-notes/v<version>.md` in the release commit;
`release-local-images.yml` uses it as the description when the tag is pushed.
Without it the page falls back to GitHub's list of merged pull requests — a
wall of internal titles that answers nothing a person arrived with.

The changelog entry (`lemma-frontend/content/changelog/`) is the full account;
the release notes are the page in front of the download button. Keep them
short, in this order:

1. **Download** — what to download for which machine, that an installed app
   updates itself, and the one-line install for the CLI and both SDKs.
2. **What's new** — five or six bullets, each an effect a person would notice,
   in plain words. No pull request numbers, no internal names.
3. **Upgrading** — only when something breaks for API, SDK or CLI users: one
   sentence and a link to the changelog's compatibility section.
4. **Links** — the changelog, and the `compare` view for everything else.

[`v0.9.0.md`](release-notes/v0.9.0.md) is the model. To fix a published
release's description, edit the file and run
`gh release edit v<version> --notes-file docs/release-notes/v<version>.md`.

Skew between an installed client and the server it is talking to never depended
on this label anyway. `lemma_sdk._spec_info.SPEC_SHA256` fingerprints the schema
itself, so `lemma doctor` reports a mismatched client whether or not anyone
remembered to bump.

## Adding a component

Add it to `SOURCES` in `scripts/check_version_consistency.py` the moment it
carries a version somebody can read — installed from an index or not. Three
packages were left out because nothing publishes them, drifted a release behind
without anything saying so, and were found by the next release rather than by
the check.

The move from API `4.0.1` to `0.6.3` was a one-time beta normalization. It
aligned the previously independent API counter with the Python and TypeScript
SDKs without claiming a stable `4.x` compatibility contract before the platform
has reached `1.0`.
