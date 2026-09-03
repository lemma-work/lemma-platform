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

Migration `0029_github_app_reauth` marks every native GitHub account
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

## Triggers

The App has one webhook URL and its installation decides which repositories it
covers, so every event for every organization arrives at the same endpoint:

    POST <api>/webhooks/github

There is nothing to subscribe to per schedule — which is why provisioning a
GitHub trigger creates no remote subscription and says so explicitly rather than
doing nothing quietly.

What separates one schedule's events from another's is the routing key,
`{source, installation_id, event}`, bound onto the schedule when it is created
from the account and the trigger. `installation_id` is what makes it
tenant-scoped: without it a `pull_request` schedule in one organization would
fire on another organization's pull requests. A schedule may narrow further by
`repository_id` (numeric, so a rename does not break it) and by `actions`.

Deliveries are verified against `CONNECTOR_GITHUB_APP_WEBHOOK_SECRET`, with
`..._PREVIOUS` accepted alongside it so a rotation is not an outage — a stream
of 403s is indistinguishable from an attack, and GitHub answers it by disabling
the hook.

Redeliveries do not fire a schedule twice. `X-GitHub-Delivery` is per-delivery
and GitHub issues a new one when it retries, so the idempotency key is derived
from the event's own content instead.

A pull request that fires an agent binds the conversation to the repository and
its head branch, and the clone runs as the schedule's connected account — the
person, not the App — so what the agent pushes is attributed to them.

## What a triggered agent needs beyond the connection

A connected account is not on its own enough for an agent to use `git` and `gh`
in its sandbox. A scheduled run is a *delegated workload*, and the workspace
credential bridge resolves the account through the same authorization the
connector tools use. Two grants are involved and only one of them is obvious:

| Grant | Given to | Why it is not enough on its own |
|---|---|---|
| `connector.use` on `github` | a **role** (POD_ADMIN, …) | Authorizes the *person*. A delegated workload is refused with `MISSING_WORKLOAD_RESOURCE_GRANT`. |
| `connector.use` on `github` | the **agent** (`PUT /pods/{pod}/agents/{name}/permissions`) | This is the one that carries a triggered run. |

Without the agent's own grant the failure is quiet in the way that matters: the
credential bridge resolves nothing, caches "unavailable" for the session, and
the checkout fails with git's own `could not read Username for 'https://github.com'`
inside the sandbox. Nothing upstream logs an error, because nothing upstream
went wrong.

## Uninstalling

Nothing has to be subscribed for this. GitHub delivers `installation` events to
an App whether or not they appear in its event list -- observed live:
`installation.new_permissions_accepted` arrived twice while the App's `events`
contained neither `installation` nor `installation_repositories`. The seven
trigger events do have to be ticked; these do not.

An `installation` delivery with `deleted` or `suspend` retires what the
installation leaves behind: its accounts go to `REAUTH_REQUIRED` and its
schedules are deactivated with `deactivated_reason` recorded in their config.
Neither is deleted — reconnecting and reactivating is enough, and the routing
key survives so nothing has to be rebuilt.

Both are treated the same. A suspended installation issues no tokens and sends
no deliveries; the only difference is that it can be undone, and reconnecting is
how you undo it either way.

## Settings

| Env | Needed for |
|---|---|
| `CONNECTOR_GITHUB_CLIENT_ID` / `_SECRET` | The OAuth half — connecting an account at all |
| `CONNECTOR_GITHUB_APP_SLUG` | Sending someone to install the App |
| `CONNECTOR_GITHUB_APP_PRIVATE_KEY` or `_PATH` | Minting installation tokens |
| `CONNECTOR_GITHUB_APP_WEBHOOK_SECRET` | Verifying inbound deliveries |
| `CONNECTOR_GITHUB_APP_WEBHOOK_SECRET_PREVIOUS` | Accepted alongside it, so a rotation is not an outage |

Without the private key everything still works as the user; only the
installation half goes quiet. Without the webhook secret, deliveries are
refused rather than trusted.
