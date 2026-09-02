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

## Connecting an account

The catalog sends people to the App's **installation** page rather than to
`login/oauth/authorize`:

    https://github.com/apps/{CONNECTOR_GITHUB_APP_SLUG}/installations/new

The slug is filled from the environment at request time — it identifies one
particular App, and there is a different one per environment, so it is
deployment configuration rather than catalog data. If the variable is unset the
connector reports itself unconfigured, which is the truth: an unfilled URL is a
404 with no explanation.

Authorizing without installing is the failure this avoids. A user token from a
GitHub App can only reach repositories the App is installed on, so
`login/oauth/authorize` yields a token that works, belongs to the right person,
and can see nothing.

Because the manifest sets `request_oauth_on_install`, the install redirects back
carrying `code`, `installation_id` and `setup_action`. The installation id is
recorded on the **account**, in `external_ref` — not on the install config,
which every account under it shares. One Lemma install of the App serves every
organization that authorized it, and each of those has its own installation; a
shared field would hand one organization's token to another's account.

## Reconnecting after the cutover

Migration `0028_github_app_reauth` marks every native GitHub account
`REAUTH_REQUIRED`. Tokens minted under the old OAuth App belong to an
application the deployment no longer holds the secret for — they cannot be
refreshed and cannot be revoked from here — and they carry no installation, so
nothing could mint an installation token for them.

The rows are marked, not deleted. Four things reference an account without a
foreign key to it: tool grants, a conversation's `metadata.repo.account_id`, pod
bundle bindings, and pod publish's required `account_id`. Deleting the rows
would silently break sandbox `git` and pod publishing for work that exists
today; reconnecting repairs them in place. Composio-brokered GitHub accounts are
untouched.

One consequence worth expecting: after the cutover the project picker lists only
repositories the App is installed on, which is usually fewer than a classic
OAuth App showed. Installing on more repositories is the fix, not a broader
scope.

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
