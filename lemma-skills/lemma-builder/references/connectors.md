# Connectors

Connectors are the pod's hands in the outside world — Gmail, Slack, Notion, Google
Calendar, and the rest. A workload (function or agent) executes an **operation**
against a third-party app on the **invoking user's behalf**, never touching raw
credentials. So an agent that "sends an email" is really running one delegated
operation through that user's connected account.

> Grounds in `pod-model.md` (connectors are org-global capabilities). This is the
> build + CLI view; the `lemma-user` skill is the operator view of the same
> commands.

## The model, for connectors

Four entities stack up — find the one you need and address it by **name**:

1. **Connector** — a catalog entry: `gmail`, `slack`, `notion`, `googlecalendar`.
   Org-global, read-only. (`lemma connectors list` / `get`.)
2. **Auth config** — the org's **credential setup** for one connector: which
   kind, which OAuth app or API-key scheme. An org can hold **several per
   connector** (two Slack apps, several MCP servers); exactly one is the
   **default** that a bare connector id resolves to. Each is identified by a name
   you choose (`workspace-gmail`), and every operation/trigger command is scoped
   by **that name**, not the bare connector id.
3. **Account** — a **per-user connected credential** under an auth config (one
   OAuth account, one bot token). Each pod member connects their own; a workload
   resolves *the invoking user's* account automatically.
4. **Operation** / **Trigger** — what you can *do* (`gmail_send_email`,
   `chat_post_message`) and what can *wake* the pod (`new email received`). Both
   are **kind-specific** — see below.

**Kind — how a connector is implemented.** A connector advertises one or more
**kinds**: `package` (native Lemma), `composio`, `http` (OpenAPI), `sql`, `mcp`.
Several connectors (gmail, slack, googledrive, jira) ship as both `package` and
`composio`. The org picks a kind with `--kind` when it creates the auth config,
and **that choice determines the operation and trigger set** — operation ids *and*
payload shapes differ between kinds. A payload that works on `composio` will not
work on `package`. The auth-config *name* encodes the choice, which is why every
command is keyed by it.

> `provider` / `AuthProvider` is the retired name for this axis. If you see
> `--provider` in older notes, the flag is `--kind`.

**Delegated identity.** When a function or agent runs, it acts as the user who
invoked it (`pod-model.md` → delegated identity). So a granted connector resolves
to *that user's* connected account. The workload only needs the
`connector.use` grant; it never sees, stores, or passes the credential.

## Do it in one call — `connectors run`

`run` resolves the whole chain (connector → install → operation → input schema)
and executes, so the common case is one command instead of four:

```bash
lemma connectors run gmail "list recent emails" --dry-run   # resolve + print the input schema
lemma connectors run gmail GMAIL_FETCH_EMAILS -d '{"max_results": 5}'
```

- The first argument is a **connector id** or an install name; a bare connector id
  resolves to its default (or only) install.
- The second is an **operation id** or a plain-English intent. The resolved id is
  always printed, so the next run can name it exactly.
- `--dry-run` stops after resolving and prints the input schema. Omitting `--data`
  on an operation that requires input does the same rather than failing.
- **An inferred write is refused.** Intent matching is lexical, so "list recent
  emails" can rank a label-editing operation above a fetch. A read-shaped intent
  prefers a non-mutating match, and an operation that changes data *and* was
  inferred rather than named will not run without `--yes`. Name the operation id
  for anything you intend to repeat.
- `--account` takes an account id **or** the email it was connected with.

## The wider picture — `overview`

Operations and triggers are addressed by **auth-config name** and differ per kind.
Every command also accepts the bare connector id, so `overview` is where you go to
see everything rather than a mandatory first step:

```bash
lemma connectors overview     # table: App | Auth Config | Kind | Status | Accounts
lemma connectors status       # same facts, your installed apps + your connected accounts
```

`overview` prints one row per installed auth config — the **Auth Config** column is
the exact string to pass to `operations` and `triggers`. If only one auth config
exists, the CLI auto-discovers it and you can omit the name.

## Set up a connector (CLI — not bundles)

Connectors are **org/pod runtime state and do NOT round-trip in bundles**
(`pod-model.md` → authoring). Set them up by CLI and record the commands in the
pod README so anyone can reconnect after import.

```bash
# 1. Browse the catalog
lemma connectors list
lemma connectors get gmail

# 2. Create the org auth config (required before any operation/trigger command)
lemma connectors auth-configs create gmail --name workspace-gmail --kind package   # composio | http | sql | mcp
lemma connectors auth-configs list
lemma connectors auth-configs get workspace-gmail

# 3a. OAuth app: open a connect-request link, user completes it in the browser
lemma connectors connect-requests create gmail --auth-config-id <auth-config-id>
lemma connectors accounts list --app gmail            # confirm an account appears

# 3b. Token / API-key app: create the account directly with credentials
lemma connectors accounts create --auth-config workspace-gmail --file payloads/account.json

# Confirm the whole picture
lemma connectors overview
```

`auth-configs` and `accounts` both support `list` / `get` / `create` / `delete`.
`auth-configs update` additionally carries `--default/--no-default` (which install a
bare connector id resolves to) and `--status ACTIVE|DISABLED`; `auth-configs
refresh-operations` re-syncs the operation catalog.

## Discover → execute (never guess)

The discovery loop is non-negotiable: operation ids and payload keys are
kind-specific, so **search by intent, read the schema, then execute**.

```bash
# 1. Search by intent — returns ranked matches for THIS auth config's kind
lemma connectors operations search workspace-gmail "send email" --limit 5

# 2. Read the input schema (one or more ops; --details for the whole batch)
lemma connectors operations get workspace-gmail gmail_send_email
lemma connectors operations details workspace-gmail gmail_send_email slack_chat_post_message

# 3. Execute — payload goes under "payload"; pin an account only when needed
lemma connectors operations execute workspace-gmail gmail_send_email \
  --data '{"payload": {"recipient_email": "a@b.com", "subject": "Hi", "body": "Test"}}'

lemma connectors operations execute workspace-gmail gmail_send_email \
  --account <account-id> --file payloads/send.json
```

- `operations search` scans names + descriptions and returns ranked hits **for the
  auth config's kind only**. `operations list` is the same with no query.
- **The install argument is optional.** `operations search "send email"` with no
  connector searches **every installed connector** and labels each hit with the
  `auth_config` to pass on — you don't need to know which connector provides what.
  Naming one scopes the search and costs a single request.
- Search results include `input_schema` by default when `--limit` is 5 or fewer
  (`--with-schema` / `--no-schema` to force either way), so a short result list
  usually needs no follow-up `get`.
- `operations get` shows one operation's input schema; `operations details` takes
  several names (or none → every operation) and returns their schemas as a batch.
- Operation names are **case-insensitive** for `get`/`details`/`execute`, but use
  the spelling `search` returned (`gmail_send_email`, not a guessed
  `GMAIL_SEND_EMAIL`).
- `execute` expects the operation payload under a top-level `"payload"` key; pass
  `--account <id>` to pin a specific connected account, otherwise the invoking
  user's account is resolved.

**Not sure which operation?** Run `operations search` with the intent in plain
words — it ranks over names *and* descriptions, so "send email" finds
`gmail_send_email` without you knowing the id. Ranking is lexical, though, so
**check what it picked before you act on it**: a read intent can match a
mutating operation.

## Skill guide per connector

Each connector ships a generated skill doc **per kind**. Fetch it before
writing payloads — it auto-resolves the kind from your installed auth config:

```bash
lemma connectors describe gmail              # kind auto-detected from the auth config
lemma connectors describe gmail --kind composio   # force a kind
```

(SDK: `pod.connectors.apps.skill("gmail", kind="package")`.)

## From functions and agents

Grant the connector to the workload, then call it with the **auth-config name** and
the payload you tested in the CLI. The grant is by connector id, name-based and
portable across pods:

```json
{ "resource_type": "connector", "resource_name": "gmail",
  "permission_ids": ["connector.use"] }
```

`lemma agents grant <agent> connector:gmail:use` writes the same grant (`app:` is
an accepted alias for `connector:`; prefer `connector:` so grants read the same
everywhere). Then in code:

```python
# Send an email as the invoking user
sent = pod.connectors.execute(
    "workspace-gmail",                 # auth-config NAME, not the bare "gmail"
    "gmail_send_email",                # operation id from `operations search`
    {"recipient_email": data.to, "subject": data.subject, "body": data.body},
).to_dict()["result"]

# Post to Slack
pod.connectors.execute(
    "workspace-slack", "chat_post_message",
    {"channel": "C123", "text": "Triage complete — 3 tickets resolved."},
)
```

- The response is `{"result": ...}` — unwrap with `.to_dict()["result"]`.
- **Don't pass `account_id` in code** unless you must pin one. The backend resolves
  the configured fixed account or the invoking user's connected account from the
  workload token. If the user has no connected account, the call fails with an
  account-resolution error — let that surface unless there's a meaningful fallback.
- Agents granted the connector get an operation toolset automatically; agents with
  the `WORKSPACE_CLI` toolset can also run the `lemma connectors operations …`
  commands themselves.

### Whose account: USER-owned vs a pinned shared account

Which connected account a call runs against depends on whether you pin one:

- **USER-owned (default, no `account_id`)** — the call resolves to the **invoking
  user's** own connected account. Each pod member acts as themselves; a member with no
  connected account gets an account-resolution error. This is what you want for "email
  the customer *as me*."
- **Pinned shared account (AGENT-owned)** — pass a specific `account_id` (or configure
  a fixed account on the surface/function) and every invoker uses that **one** account,
  regardless of who triggered the workload. This is the shared-sender pattern: one team
  Gmail account sends for everyone.

A pinned account owned by someone other than the invoker needs **two grants on the
workload** — `connector.use` on the connector *and* `connector_account.use` on that
account:

```json
{ "resource_type": "connector", "resource_name": "gmail",
  "permission_ids": ["connector.use"] },
{ "resource_type": "connector_account", "resource_name": "<account-id>",
  "permission_ids": ["connector_account.use"] }
```

With both grants the pinned account works for every invoker (it is invoker-independent
— the workload's grants are the authority, not the caller's identity). Note
`connector_account.manage` is a **destructive** permission gated behind approval; plain
`connector_account.use` is not. See `authorization-model.md` §8.

(App side — calling a connector operation from a browser app, with discovery and a
safe action button → `app-recipes/connector-action.md`.)

## Triggers

A connector also exposes **triggers** — events that can wake a pod (`new email
received`, `message posted`). Like operations, triggers are **scoped to an auth
config** and returned for that config's kind only:

```bash
lemma connectors triggers list workspace-gmail              # kind-scoped
lemma connectors triggers list workspace-gmail -q "new email"
lemma connectors triggers get workspace-gmail <trigger-id>  # full config + payload schema
```

A trigger id is **kind-qualified**: `{app}:{kind}:{slug}` (e.g.
`gmail:composio:new_message`). Wire a trigger to an agent or workflow with a
**WEBHOOK schedule** — see `schedules-and-triggers.md`. A trigger needs a
**connected account** to deliver events.

## Patterns

- **Outbound action from a workload.** Function/agent grants `connector.use`,
  executes one operation (send email, post message, create event) on the user's
  account. The hands-on half of most pods.
- **Inbound event → automation.** A connector trigger + WEBHOOK schedule +
  `filter_instruction` starts a workflow on real-world events (see
  `schedules-and-triggers.md`).
- **Surface.** A connector account also backs an **agent surface** (Slack/Gmail/…)
  — same account, different consumer. See `surfaces.md`.

## Limits & gotchas

- **Not in bundles.** Auth configs, accounts, and connect state are org/pod runtime
  state — `pods import` won't recreate them. Script the setup in the README, or a
  connector-using bundle is incomplete.
- **Several auth configs per connector are allowed**, with exactly one default that
  a bare connector id resolves to. Use `auth-configs update --default` to move it,
  and always address operations by auth-config *name* so you never depend on which
  one is default.
- **Kind determines everything.** Re-check operation ids and payloads with
  `operations details` whenever you switch kinds; never reuse names across them.
- **Wrong/foreign auth-config name.** `operations search` returning not-found
  usually means the name is wrong or the auth config belongs to another org. Run
  `lemma connectors overview` to read the exact name.
- **Account required for events.** Triggers (and surfaces) need a connected account
  before they deliver anything.

## Verify

```bash
lemma connectors overview                          # auth config + accounts wired?
# read-only smoke test of one operation:
lemma connectors operations execute workspace-gmail <read-only-op> --data '{"payload": {}}'
# then verify the delegated workload path end-to-end:
lemma functions run <fn-that-calls-the-connector> --data '{...}'
```

## See also

- The model → `pod-model.md` · inbound events → `schedules-and-triggers.md`
- Agents on chat platforms (same accounts) → `surfaces.md`
- Calling connectors from code → `functions.md` · from an app →
  `app-recipes/connector-action.md` · operate → the `lemma-user` skill
