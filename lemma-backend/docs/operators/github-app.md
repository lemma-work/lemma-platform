# The GitHub App

Lemma reaches GitHub as a **GitHub App**, not an OAuth App. That choice buys
three things a classic OAuth App cannot: an identity that outlives the person
who set a schedule up, per-installation rate budgets, and one webhook URL that
delivers whatever the installation can see.

One App per environment. An App has a single webhook URL and a single callback,
so a development tunnel and production cannot share one — configure a separate
App for each, and give each its own `CONNECTOR_GITHUB_*` values.

## Creating one

[`config/github-app-manifest.json`](../../config/github-app-manifest.json) is
the source of truth for what the App needs. It is checked in so the permission
set is reviewable, and so a new environment is a copy rather than a memory
exercise. Create the App from it:

    https://github.com/settings/apps/new?state=<anything>

and paste the manifest, or POST it as `manifest=<json>`. Set the two URLs first
— they are deliberately absent from the file, because they differ per
environment:

| Field | Value |
|---|---|
| Callback URL | `<api>/connectors/connect-requests/oauth/callback` |
| Webhook URL | `<api>/webhooks/github` |

For local work `make dev-public` prints the public API URL to use for both.

## Why each permission

| Permission | For |
|---|---|
| `metadata: read` | Mandatory for every App |
| `contents: write` | Cloning, pushing, branches, and the sandbox's `git` |
| `pull_requests: write` | Opening, reviewing and merging pull requests |
| `issues: write` | Issues, comments, labels, assignees |
| `actions: write` | Runs, re-runs, cancels, `workflow_dispatch` |
| `checks: read` | Reacting to `check_suite` |
| `deployments: write` | Approving pending deployments |
| `secrets: read`, `actions_variables: read` | Listing only — Lemma never writes either |

Secrets and variables are deliberately read-only. Nothing in the operation set
writes one, and an App that cannot write them cannot be made to.

## The two tokens, and which acts when

The App issues both kinds and Lemma uses both, on purpose.

**Installation token** — the App acting as itself, minted per installation and
valid an hour. This runs an agent's connector operations. A schedule keeps
working after its author leaves, and a webhook-triggered run has an identity
even with nobody present.

**User token** — the person acting as themselves, from the OAuth half of the
install. This runs the sandbox's `git`/`gh`, pod bundle publish and import, and
the fourteen operations GitHub marks as user-only (gists, `/user/...`). Work an
agent does in a checkout is attributed to the person whose repository it is,
which is the behaviour people expect from a tool that opens pull requests on
their behalf.

Which one an operation gets is not a setting. Every operation carries
`github_token_kind`, derived from GitHub's own `x-github.enabledForGitHubApps`,
so the answer comes from GitHub rather than from a list maintained here.

## Settings

| Env | Needed for |
|---|---|
| `CONNECTOR_GITHUB_CLIENT_ID` / `_SECRET` | The OAuth half — connecting an account at all |
| `CONNECTOR_GITHUB_APP_SLUG` | Sending someone to install the App |
| `CONNECTOR_GITHUB_APP_PRIVATE_KEY` or `_PATH` | Minting installation tokens |
| `CONNECTOR_GITHUB_APP_WEBHOOK_SECRET` | Verifying inbound deliveries |

Without the private key everything still works as the user; only the
installation half goes quiet. Without the webhook secret, deliveries are
refused rather than trusted.
