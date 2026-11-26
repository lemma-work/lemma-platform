# Simpler connections, and a Lemma that works as the organization's GitHub bot

**Status:** agreed on 2026-11-26 and built. The two points under "What
reviewers must agree to" change the pod connector model and need a yes from
its owners before this merges.

The page follows an approved mock, "Lemma Connections, Simpler".

This document covers three backend changes and the page built on them:

- **A.** Connecting from a Lemma adds the app to that Lemma.
- **B.** One read: "my connections here".
- **C.** A third pod connector mode, `APP`: the organization's GitHub App, with
  no person's account behind it.
- **D.** The Connections page, rebuilt on A to C.

The rule that makes pod connectors safe is kept throughout: **the connector
decides whose identity a call uses; no caller, workload or model can.** One
rule stays strict while adding gets relaxed: **nobody can put someone else's
account into use.** "Mine, for everyone" stays the account owner's own act.

---

## Why

Two things are wrong today.

**The page shows the backend's model.** It has about eleven ideas on one
screen: connectors, slugs, modes, "required", purpose, accounts, three
visibilities, two defaults, the sandbox switch, Withdraw versus Remove, and
"Bring another of yours in".

**Its two lists can disagree.** A person can connect GitHub and still see
"Connect GitHub". Their account is in the Lemma, but the pod connector is
declared only on the first call (`ensure_for_installation`). The page's
`ConnectOffer` checks only the connector list, so the banner never goes away.

**A Lemma can only have one GitHub identity by borrowing a person's.** A
`SHARED` connector runs on a member's shared account. Sharing is person-only
for app pages (`require_person` in `api/pod_connector_controller.py`, #1039 and
#1059), so plugin set-up dead-ends. Reads also go out under the sharer's name
and rate limit, and the Lemma loses GitHub when that person leaves.

---

## A. Connecting from a Lemma adds the app to it

- **Connect request.** `POST /organizations/{org}/connectors/connect-requests`
  takes an optional `pod_id`. It is kept in the request's `attributes`
  (`attributes.pod_id`), which is existing JSONB, so it needs no migration. The
  person must be a member of that pod, checked when the request is made.
- **When the connect completes** (the OAuth callback, or the immediate
  credential form), on a request that carries a `pod_id`, as that person:
  1. `ensure_for_installation(pod, auth_config, source=AUTO)`. They now have an
     account, so this is allowed. It reuses an existing `PER_MEMBER` connector
     on that installation, or declares one.
  2. Add the new account to the pod as their member default (`picker.add_own`,
     `as_default=True`), so the row reads "Connected" at once.

  A failure in either step is logged and never fails the connect. The account
  is connected either way, and the next call declares the connector as it
  does today.
- **Use it here** (an account they already have elsewhere in the org).
  `POST /pods/{pod_id}/connector-accounts` (which adds a pod account) also
  ensures a `PER_MEMBER` connector exists for that installation, and makes the
  account their member default when they have none. This route stays
  person-only: it puts the person's own account in play.

## B. One read: my connections here

`GET /pods/{pod_id}/connections` returns one row per app the Lemma uses,
judged for whoever is asking:

```jsonc
{
  "may_manage": true,              // pod.connector.manage
  "github_bot": {                  // null when the org has no usable GitHub App installation
    "installations": [{ "login": "lemma-platform", "repository_selection": "selected" }]
  },
  "items": [{
    "slug": "github",
    "connector_id": "github",
    "title": "GitHub",
    "icon": "https://…",
    "mode": "PER_MEMBER",          // PER_MEMBER | SHARED | APP
    "purpose": "Reads pull requests and posts reviews.",
    "required": false,
    "status": "READY",             // READY | NEEDS_ACCOUNT | NEEDS_INSTALLATION | UNAVAILABLE
    "runs_as": {                   // who a call by me runs as, in words the page can show
      "kind": "me",                // me | person | app | nobody
      "label": "ada-lovelace",   // my account's label, the sharer's account, or the bot's GitHub org login(s)
      "owner_user_id": null,       // SHARED: the sharer
      "owner_name": null
    },
    "me": {
      "standing": "ready",         // ready | connect | reconnect | use_existing | waiting | not_allowed
      "account": { "id": "…", "label": "ada-lovelace", "pod_account_id": "…" },
      "existing": null             // use_existing: { "account_id", "label" } — my org account on this installation, not in the Lemma yet
    },
    "coverage": { "connected": 2, "total": 3 }   // PER_MEMBER, may_manage only; else null
  }]
}
```

**What `me.standing` means, by mode:**

| Standing | `PER_MEMBER` | `SHARED` | `APP` |
|---|---|---|---|
| `ready` | I have a usable pod account | The shared account is up and I may use it | The installation is bound and I may use it |
| `connect` | No account of mine anywhere on this installation | — | — |
| `use_existing` | I have an org account on this installation, not in the Lemma yet | — | — |
| `reconnect` | My account needs signing in again | — | — |
| `waiting` | — | No account shared, or its owner withdrew it or left | `NEEDS_INSTALLATION` |
| `not_allowed` | I lack `connector.use` | I lack `connector.use` | I lack `connector.use` |

A disabled installation shows `status: UNAVAILABLE` and `waiting`.

This is built from what already exists:

- `list_by_pod`
- `connector_status`
- `can_use` (`services/pod_connector_use.py`)
- the person's org accounts
- `pod_connector_coverage`, for editors

It is read-only. It never declares or adds anything.

## C. `APP`: the organization's GitHub bot

A pod connector in mode `APP` acts as the organization's GitHub App on every
call, reads included. Nobody connects or shares anything. The only set-up is an
org admin installing the App on GitHub, which already binds the installation to
the org (`connector_installations`, migration 0063).

### Data model: no migration

- `mode` is `VARCHAR(20)` with no check constraint, so `"APP"` is a new
  `PodConnectorMode` value and nothing more.
- `auth_config_id` still names the org's GitHub auth config.
  `shared_pod_account_id` must be null.
- **The connector stores no installation.** An org can have the App on several
  GitHub accounts (`lemma-platform`, `acme`). The bot picks the installation
  **per call**, from the repo owner the call names. That is how GitHub Apps
  find their installation anyway.
- **Which installations count:** bound to the pod's organization, **proven**
  (`proven_at` not null), and on a GitHub **Organization**, not a user.
  - An unproven binding may have been squatted on before H4.
  - A personal installation would hand someone's own repos to a shared Lemma.
- **Each installation's GitHub account** (`account_login`, `account_type`,
  `repository_selection`) is read from GitHub with the App's JWT
  (`GET /app/installations/{id}`). It is kept in the binding's existing
  `details` JSONB (`connector_installations.metadata`), filled on first need
  and refreshed when stale.
- **Status `NEEDS_INSTALLATION`** is a new computed `PodConnectorStatus`: the
  org has no installation that counts.
- **Validation:**
  - `APP` is allowed only when `is_github(connector_id)`.
  - Never with a shared account.
  - Only `POD` visibility for now, the same default every connector has.

### Who may add it, and who may use it

- **Adding** (`POST /pods/{pod_id}/connectors` with `mode: "APP"`) needs
  `pod.connector.manage`, like every other connector. It is **not**
  person-only: it puts nobody's credential in play, so a plugin can set itself
  up from inside its app. The org admin's install, and the repos they gave the
  App on GitHub, are the consent and the reach.
- **Using** works as for `SHARED`, minus the account half: `connector.use` on
  the pod connector. A call with nobody behind it (a schedule, a webhook, a
  contact) is served when `connector.use` allows it.
- **Default slug:** `github` when it is free, otherwise `github-bot`. When both
  modes are present, agents see from each connector's description which
  identity it uses.

### Resolution

- `PodAccountPicker.pick` gains an `APP` branch. It returns
  `ResolvedPodConnector` with `pod_account=None`, `account=None` and
  `app_installations` = the candidates above. `resolve` and `resolve_standing`
  pass it through.
- `serve_installation`, the deprecated route, **never** resolves to `APP`.
  `ensure_for_installation` keeps declaring `PER_MEMBER` only.
- **Other places that use the resolved account:** surfaces' credential
  resolver, the sandbox GitHub credential, bundle publish, and the rest of the
  callers of `resolve_pod_connector` and `resolve_standing_pod_connector`.
  Each refuses an `APP` connector with the typed `APP_CONNECTOR_UNSUPPORTED`
  (409, `details.pod_connector`). Only operations and schedules accept it in
  this version.

### Execution

`ConnectorOperationService.execute` takes an installation path when the
connector is `APP`:

1. **Pick the installation.** Use the operation payload's `owner`, else `org`,
   else the owner part of `repository` or `full_name`, and match it
   case-insensitively against the candidates' `account_login`.
   - No owner named, one candidate: use it.
   - No owner named, several candidates: refuse with `APP_INSTALLATION_AMBIGUOUS`
     (409). The details list the logins.
   - An owner that matches none: refuse with `APP_NOT_INSTALLED` (409). The
     details carry the owner and the install URL (`github_install_url`).
2. **Build the plan:**
   - `third_party_credentials = {}`
   - `account_external_ref` = the installation id
   - `act_as = "app_only"`, always, whatever the caller passed
   - `account_id` and `account_user_id` = `None`
   - `organization_id` = the auth config's org
   - `acting_user_id` unchanged

   The plan already tolerates having no account.
3. **The presenter mints as it does today.**
   - A `user_only` route is already refused with `APP_IDENTITY_UNAVAILABLE`;
     the refusal gains `details.reason = "user_only_operation"` and the slug.
   - A failed mint becomes `CONNECTOR_UNAVAILABLE`, not a 500.
4. **Listings for an `APP` connector hide `user_only` operations.** This is a
   per-mode filter in `operation_visibility.py`, so agents aren't offered calls
   that can only fail.

### #1248 (merged, 84d813777)

- On an `APP` connector, `act_as` has nothing to choose: every call is the App.
- `require_function_run` (only a function may *ask* for the App) is skipped
  for `APP` connectors. The connector's declaration is the consent, and
  `connector.use` is the gate.
- The agent's fallback to the person doesn't apply, because there is no
  person.
- `PER_MEMBER` and `SHARED` connectors behave exactly as before.

### Schedules and webhooks

- Webhook routing already keys on the installation id and the bound org
  (`webhook_sources/github.py`, `bound_organization`).
- `external_schedule_writer._github_binding` takes the installation id from
  the resolved account today. For `APP` it takes it from the schedule's
  repository filter's owner, picked as for an operation; with one candidate it
  uses that one, otherwise it refuses with `APP_INSTALLATION_AMBIGUOUS`.
- `require_connector_use` and `resolve_as_owner` go through the `APP` branch.
  No person is needed, so the schedule outlives its owner's membership.
- Test: an e2e test that binds a schedule to an `APP` connector, delivers a
  signed `pull_request` webhook for that installation, and sees the run.

### Bundles

- `lemma-pod-bundle`: `POD_CONNECTOR_MODES` gains `"APP"` (`apply_fields.py`).
- **Import:** an `APP` connector needs no installation or account choice. It is
  declared as `APP` whatever the org has, and reads `NEEDS_INSTALLATION` until
  an admin installs the App.
- **Export** writes `mode: APP`.

### Record

`connector.operation_executed` (`domain/analytics.py`) gains properties:

- `identity`: `"app"` or `"user"`, from what was actually presented
- `pod_connector`: the slug
- `installation_id`: for `"app"`

The actor stays the person who asked.

---

## D. The page

The page is built to the mock, on read B. In short:

- **One row per app.** Each says whether it works for me and whose account it
  runs as, with one button: Connect, Use it here, Reconnect, or Ask.
- **No slugs** unless two connectors share an app.
- **A sheet per app:**
  - Runs as: each person's own, Mine for everyone, or the bot (when
    `github_bot`)
  - the sandbox switch, only once shared
  - your account
  - who's connected
  - what it's for
  - Disconnect and Remove
- **Add an app:** pick an app and you're signing in, with no form. For GitHub
  with a bot available, it offers "Use the bot" first.
- **A link to `?connector=…` opens that app's row**, or its sheet. There is no
  separate banner any more.
- **"Share with some people" is left out of the page.** The backend keeps it.

---

## Not in this round

- **Changes that wait:**
  - Collapsing account visibility into the connector (proposed "change 3").
  - Retiring `serve_installation`.
- **The plugin kit's Connect** (`_kit` in awesome-lemma). It uses A to C once
  they land.
- **Sandbox `git`/`gh` as the bot.**
- **Bot modes for other apps** (Slack's manager bot is the same shape).

## What reviewers must agree to

1. **A pod connector can resolve to no account** (`APP`). Outside operations
   and schedules, it is refused with `APP_CONNECTOR_UNSUPPORTED`.
2. **Adding connectors is relaxed:**
   - Anyone with `pod.connector.manage` may add the org's bot, including from
     an app page.
   - #1248's "only a function may present as the app" doesn't apply on an
     `APP` connector.

   The trade: an editor with no GitHub access of their own can have the bot
   act on the repos the org gave it.
