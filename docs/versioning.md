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

## Publishing

Pushing the tag `v<version>` is the whole release. Every package workflow, the
image release (which creates the GitHub Release and dispatches the Desktop
build on the tag) and the Slack tracker start from that one push.

| Package | Registry | Workflow |
|---|---|---|
| `lemma-sdk` | PyPI | `release-lemma-python.yml` |
| `lemma-terminal` | PyPI | `release-lemma-terminal.yml` |
| `lemma-sdk` | npm | `release-lemma-typescript.yml` |

`PACKAGES` in `scripts/release_registry.py` is that table. The tracker, the Slack
summary and `tests/test_release_version_resolution.py` all read from it, so a new
package goes there first, and the tests then hold its workflow to the standard.
Every package workflow does the same things, in this order:

1. **Triggers**: a `v*` tag push, a published release, and a dispatch with a
   version. Only the tag push is reliable: a release created by a workflow
   raises an event GitHub will not start runs from.
2. **Resolve the version** with `release_registry.py version`, from the tag on
   a push and from the input on a dispatch. A blank or malformed version fails
   the run.
3. **Ask the registry** with `release_registry.py published`. If it already
   serves this exact version, every later step is skipped and the run succeeds.
   Re-running a release is a no-op, and a repair is a plain re-run. If the
   registry cannot be reached, the run fails rather than guessing.
4. **Verify, gate, build and publish.** The tests run on the release commit,
   and `check_version_consistency.py --expect` runs before anything is
   uploaded. The CLI also waits for its own `lemma-sdk` to appear on PyPI.
5. **Confirm** with `release_registry.py wait` that the registry now serves the
   version. An upload that "succeeded" but cannot be installed fails here.

No job has a condition of its own: anything that skips work does it as a
visible step.

### Following a release in Slack

`release-announce.yml` starts with the tag and posts to the release channel
through the `SLACK_WEBHOOK_URL` secret, the same webhook the failure notifier
uses:

- that the release started, with the commit and a link to the tracking run;
- each package as its registry begins serving the exact version, with a link
  to that version on PyPI or npm, followed by the GitHub Release and Desktop;
- each run of a release workflow that fails, with a link to the run;
- a final summary that says which registries have the version and which do
  **not**, with anything missing named in the first line.

It stops once every component has landed. Otherwise it stops once nothing has
been running or changing for fifteen minutes, or after three and a half hours.
If anything is missing at that point the job fails, and `notify-failure.yml`
announces that too. To follow a repair, re-run the failed publish and dispatch
"Announce a release" with the version. It also reports the hand-dispatched
runs that started while it was watching.

`lemma-stack` and `lemma-pod-bundle` carry the release version but are not
published to an index. `install.sh` and `install.ps1` install `lemma-stack`
from this repository with `uv tool install`, and the CLI wheel vendors
`lemma_pod_bundle`.

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
