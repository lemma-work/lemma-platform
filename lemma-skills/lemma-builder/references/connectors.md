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
**kinds**: `http` (an OpenAPI descriptor Lemma executes directly — this is what
"native" means), `composio`, `sql`, `mcp`. Several connectors (gmail, slack)
ship as both `http` and `composio`. The org picks a kind with `--kind` when it
creates the auth config, and **that choice determines the operation and trigger
set** — operation ids *and* payload shapes differ between kinds. A payload that
works on `composio` will not work on `http`. The auth-config *name* encodes the choice, which is why every
command is keyed by it.

**Kind is one discriminator over three independent axes**, and knowing which axis a
question belongs to saves a lot of guessing:

| Axis | What it says | Where it lives |
| --- | --- | --- |
| **Auth** | `OAUTH2`, `API_KEY`, or `NOAUTH` — and whether the org may bring its own OAuth client or must use Lemma's system credentials | the kind's `auth_scheme` on the catalog entry |
| **Discovery** | where the operation list comes from: `none` (the catalog already holds them — Composio toolkits, and connectors whose spec is curated at build time such as GitHub, Slack and Gmail) or `mcp` / `openapi` (discovered *per install* and stored against the auth config) | the kind's `discovery` |
| **Execution** | how one operation is actually called — an `execution` descriptor per operation, always present | the operation row |

They move independently. That is why `auth-configs refresh-operations` exists only
for MCP/OpenAPI installs (the discovery axis), why an org can hold two installs of
one connector with different credentials but the same operations, and why the same
operation can present as two different callers — see below.

> `provider` / `AuthProvider` is the retired name for the kind axis. If you see
> `--provider` in older notes, the flag is `--kind`.

**Delegated identity.** When a function or agent runs, it acts as the user who
invoked it (`pod-model.md` → delegated identity). So a granted connector resolves
to *that user's* connected account. The workload only needs the
`connector.use` grant; it never sees, stores, or passes the credential.

**Who the call presents as is a second question.** For most connectors the answer is
always "the person". Where a connector is backed by an **app installation** — GitHub
today — a call can present either as the person or as the **app**, and the caller
decides: an **agent's** connector operations ask to act as the app, so a schedule
keeps working after the person who set it up leaves the team, while pod publish, pod
import, and the sandbox's own `git` / `gh` act as the **person**, so commits carry
their name. The app's token is used only when the caller asked for it, the route
allows it (GitHub's own metadata says which do; `/user/...` routes never can), and
the account knows its installation — anything else falls back to the person's token.
Nothing about this is a setting to maintain.

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
lemma connectors auth-configs create gmail --name workspace-gmail --kind http   # composio | http | sql | mcp
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
refresh-operations` re-syncs the operation catalog — only meaningful for the
per-install discovery kinds (`mcp`, `http`), since every other kind's operations come
from the catalog.

## Custom connectors — an app that is not in the catalog

The catalog is not the limit. Three of its entries are **generic**: they carry no
app of their own and become one when an install points them at an address. This
is how a pod reaches an internal API, a warehouse, or any MCP server, and it is
the answer whenever `lemma connectors list` does not have what you need.

| `connector_id` | `--kind` | The install config holds |
| --- | --- | --- |
| `openapi` | `http` | `spec_url` **or** `spec_inline`; optional `server_url` (overrides the spec's), `default_headers` |
| `mcp` | `mcp` | `server_url`; optional `extra_headers`, `session_setup` |
| `sql` | `sql` | `dialect`, `host`, `port`, `database` |

The install carries the **address**; the account carries the **credentials**.
That split is the thing to get right — a key on the install is rejected, and an
address on the account is too.

```bash
# An OpenAPI-described API. Operations are discovered from the spec at install.
lemma connectors auth-configs create openapi --kind http --name acme-api \
  -d '{"spec_url": "https://api.acme.test/openapi.json"}'
lemma connectors accounts create --auth-config acme-api -d '{"api_key": "sk-..."}'

# An MCP server. Its tools become operations.
lemma connectors auth-configs create mcp --kind mcp --name acme-mcp \
  -d '{"server_url": "https://mcp.acme.test/mcp"}'

# A Postgres database, read-only.
lemma connectors auth-configs create sql --kind sql --name warehouse \
  -d '{"dialect": "postgresql", "host": "db.acme.test", "port": 5432, "database": "analytics"}'
lemma connectors accounts create --auth-config warehouse \
  -d '{"username": "reader", "password": "..."}'

# Then the ordinary loop — the operations came from the server, not the catalog.
lemma connectors operations search acme-api "create an invoice"
lemma connectors run acme-api <operation-id> --dry-run
```

**Credentials per kind** (they go on the *account*):

- `openapi` — `access_token` and/or `api_key`, both optional.
- `mcp` — `bearer_token`, optional (see OAuth below).
- `sql` — `username` and `password`, both **required**.

### Which credential a custom install wants — read the install, not the kind

`mcp` is one catalog entry standing for every server a tenant may point at, and
they do not agree on how to authenticate. So Lemma asks the server. At install
time it tries RFC 9728 → RFC 8414 → RFC 7591 **dynamic client registration**: if
the server describes its own authorization, the install is registered as an OAuth
client and its people sign in through a browser. If not, it stays paste-a-token.
Registration failing is never fatal — you get the token path.

The consequence you must code against:

```bash
lemma connectors auth-configs get acme-mcp    # read `auth_scheme` on the INSTALL
```

The catalog entry says `API_KEY`. An install whose server negotiated
authorization answers `OAUTH2`. **Branch on the install's `auth_scheme`, never on
the connector's kind** — posting an empty credential set to a server that wanted
a sign-in produces an account that looks connected, holds no token, and fails
every call.

### Rules that bite

- **The config schemas are closed.** `additionalProperties: false`, validated on
  create *and* update. An unknown key is refused, not quietly stored — which is
  the point, since a stored stray key was once read back as a credential.
- **Private and link-local addresses are refused.** `169.254.169.254` and
  friends, on `server_url`, `spec_url` and the SQL host. A connector is not an
  SSRF tool.
- **`sql` is PostgreSQL and read-only.** Statements are parsed and non-`SELECT`
  ones refused. Three operations: `execute_query`, `list_tables`,
  `describe_table`.
- **`kind`, `connector_id` and `config_source` are immutable** after create.
  Changing where an install points is a new install, not an update.
- **Changing the address invalidates accounts rather than deleting them.** Edit
  `server_url` / `spec_url` / `spec_inline` and the accounts on it go
  `REAUTH_REQUIRED`.
- **`refresh-operations` is the recovery path** when a spec or a tool list
  changes. It answers `200` whether or not the server replied — **read `status`**;
  `failed` means the server refused.
- **Config comes back redacted** on read, and secrets nested inside it too.

### Composio toolkits Lemma cannot sign in to

Composio brokers every toolkit on Lemma's account, but it only holds **managed
OAuth credentials for some of them**. Where it holds none, the catalog says so:

```bash
lemma connectors get twitter     # kinds[].system_default_available
```

Two shapes, and the catalog picks whichever costs the org less:

- **The toolkit also takes an API key** — Shopify, Meta Ads, PostHog, Metabase
  and a dozen others. The entry reads `API_KEY`, `system_default_available:
  true`, and it connects like any other key app: nothing for the org to set up,
  a token pasted when someone connects their account.
- **The toolkit is OAuth only** — Twitter, Spotify, TikTok, LinkedIn Ads, Google
  Chat, Google Contacts, Google Forms. The entry reads
  `system_default_available: false`, the UI offers *Set up* rather than
  *Connect*, and the install needs the app's own OAuth client:

```bash
lemma connectors auth-configs create twitter --kind composio --name acme-twitter \
  --config-source ORG_CUSTOM \
  -d '{"client_id": "...", "client_secret": "...", "generic_id": "..."}'
```

The fields are **whatever that toolkit asks for** — read them off
`kinds[].install_config_schema`, which the catalog derives per toolkit. Twitter
wants a third one beyond the client id and secret; most want only those two. A
`--config-source SYSTEM_DEFAULT` install of one of these is refused, and says
what to supply instead: there is nothing for it to default to.

### GitHub is a first-class connector, backed by a real App

Worth calling out because it behaves differently from an ordinary OAuth connector in
three ways:

- **Connecting has two halves.** Authorizing gives Lemma a token that belongs to the
  right person and can see *nothing*, because a GitHub App's user token reaches only
  the repositories the App is installed on. The App has to be **installed** on the
  user or organization as well (`github.com/apps/<slug>/installations/new`). A
  connected account that authorized but never installed is the usual reason a
  repository "does not exist".
- **The account carries an `installation_id`.** It is resolved at connect time and
  stored on the account, not on the org install — one App can be installed on many
  organizations under one auth config, so the installation belongs to the individual
  authorization. Nothing about it is typed by hand: a webhook schedule's
  `installation_id` is bound from the connected account when the schedule is created,
  and a wrong one would route another organization's events at your pod.
- **An agent's calls act as the App.** See *Delegated identity* above: an agent's
  connector operations present as the App where GitHub permits it, so a schedule
  outlives the person who set it up, while pod publish/import and the sandbox's
  `git`/`gh` stay the person so their name is on the commits.

The rest is ordinary: `connector:github:use` on the workload, operation ids from
`operations search`, triggers wired through a `WEBHOOK` schedule.

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

(SDK: `pod.connectors.apps.skill("gmail", kind="http")`.)

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

## An agent setting a connector up for itself

Sooner or later an agent needs an API nobody has connected yet. It can do the
whole thing, but only half of it is its own: **the agent creates the install, a
person supplies the credentials.** There is no way around that second half and no
reason to want one — the credential is theirs.

**What the `CONNECTORS` toolset can do.** Four tools, all execution-only:
`list_connectors`, `search_connector_operations`, `describe_connector_operation`,
`run_connector_operation`. **None of them creates anything.** An agent that needs
a new connector needs the `WORKSPACE_CLI` toolset and runs `lemma` itself.

The loop, in full:

```bash
# 1. Is it already there? Installs, not catalog entries.
lemma connectors overview

# 2. Create the install. A custom API is the `openapi` entry (above); a catalog
#    app is its own id.
lemma connectors auth-configs create openapi --kind http --name acme-api \
  -d '{"spec_url": "https://api.acme.test/openapi.json"}'

# 3. Find out what the person has to supply.
lemma connectors auth-configs get acme-api      # read `auth_scheme` HERE
```

Then one of two branches:

**API key** — send them to the UI. **Never ask for the key in the conversation.**
A key pasted into a message is in the transcript for good, and passing it on a
command line puts it in shell history and in `/proc/<pid>/cmdline`, where
anything else in the sandbox can read it. There is no version of this worth the
convenience:

> "I've added Acme. It needs an API key — open **Connectors** in the workspace,
> find *acme-api* and add an account. I never need to see the key."

If a key has already been pasted into the conversation, say so plainly and ask
them to rotate it: it cannot be unsent.

**OAuth** — mint a link and hand it over. Do not try to follow it:

```bash
lemma connectors connect-requests create acme-api --auth-config-id <id>
# → prints an authorization_url
```

> "Sign in here to finish connecting Acme: <authorization_url>"

**Then wait for the account, and only then run anything.** An operation against
an install with no account fails with an account-resolution error, so poll rather
than guess:

```bash
lemma connectors accounts list --app acme-api     # empty until they finish
lemma connectors operations search acme-api "create an invoice"
lemma connectors run acme-api <operation-id> --dry-run
```

Two things worth saying plainly to whoever is reading the agent's output:

- **The account is per person.** Connecting it yourself does not connect it for
  the rest of the pod — each member connects their own, and a workload resolves
  the invoking user's. A single shared account is the pinned-account pattern
  above, and it needs a second grant.
- **The workload still needs the grant.** Creating the install does not grant it:
  `lemma agents grant <agent> connector:acme-api:use`.

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
- **Surface.** A connector account also backs an **agent surface** — but only for
  Slack and Teams; Telegram/WhatsApp default to a system bot and email is provisioned
  outright. Same account, different consumer. See `surfaces.md`.

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
