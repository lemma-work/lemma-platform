# Schedules And Triggers

Schedules are the pod's clock and its tripwires — they start an agent or a workflow
**automatically** when time passes, a row changes, or an external app fires an
event. Without a schedule, automation only runs when a human asks; with one, the
pod works on its own.

> Grounds in `pod-model.md` (schedules/triggers start agents or workflows). This is
> the build view; the `lemma-user` skill operates the same schedules.

## The model, for schedules

A schedule has exactly **one trigger type** and exactly **one target** (an agent
**or** a workflow). Trigger types are **time-based** (`TIME`) or **event-based** — and
an event is one of two sources, a **table change** (`DATASTORE`) or a **connector
event** (`WEBHOOK`):

- **`TIME`** — a cron expression or a one-shot timestamp. *"Every weekday at 9am",
  "once on 2026-06-14".*
- **`DATASTORE`** — a row created/updated/deleted on a named table. The changed row
  starts the run. *"When a ticket is inserted, triage it."*
- **`WEBHOOK`** — a connector event (new email, message posted). Needs a pod
  connector (by slug) and a kind-qualified trigger id. *"When mail arrives, intake
  it."*

## Whose identity does a fired run use?

This decides which rows the run can see, so it is the first thing to settle —
and it is not always the schedule's creator:

| Trigger | Runs as |
| --- | --- |
| `TIME` | the schedule's configured user |
| `WEBHOOK` | the schedule's configured user |
| `DATASTORE` on an **RLS** table | the **owner of the row that changed** (default `run_as: "ROW_OWNER"`) |
| `DATASTORE` with `config.run_as: "SCHEDULE_OWNER"` | the schedule's configured user, for every change |
| `DATASTORE` on a **shared** table, a batched fire, or a row nobody in the pod owns | the schedule's configured user |

The RLS row-owner rule is the useful one: each member's write starts a run scoped
to **that member**, over their own rows, with their `/me` and their connected
accounts. That is what makes "write a row, get agentic work done as yourself" a
one-line app feature — see `app-recipes/rls-table.md`.

Creating a `DATASTORE` schedule needs **`datastore.table.update` on the watched
table**, not just permission to write records to it — and, while it runs as row
owners (the default), **edit access to the target** too (`agent.update` /
`workflow.update`; for `POD_DEFAULT`, `pod.update`), because its instruction runs
with other people's access.

The triggering event becomes the run's **start payload**. Design the first workflow
node (or the agent's instruction) around that exact shape — see *Event payloads*.

**Target choice** (mirrors `pod-design.md`):
- → **agent** when each firing needs judgment over current state ("review stale
  tickets and nudge owners").
- → **workflow** when each firing runs the same multi-step process ("nightly: load
  batch, loop, write report").

An agent target can also be the pod's own assistant: `--agent POD_DEFAULT` (the wire
selector, not a name). **A schedule waking an agent that has no standing instruction
of its own must carry an `instruction`** saying what to do when it fires — that is
always true for `POD_DEFAULT`, and it is refused at create otherwise. `instruction`
directs the work; `filter_instruction` (below) decides whether to fire at all. They
are different fields and not interchangeable.

> **Server-side, not live UI.** A `DATASTORE` schedule reacts to a row change by
> *doing work* (starting a workload). To keep an **app's UI** fresh on row changes, use
> `datastore.watchChanges` (a client-side WebSocket) instead — see `apps.md`. Don't
> reach for a schedule when you only need the screen to update.

## Create a schedule

CLI flags cover the common cases. Exactly one of `--agent` / `--workflow` is
required:

```bash
# TIME — cron (5-field) or one-shot ISO timestamp. Read in UTC unless
# --timezone (an IANA zone, sent as config.timezone) says otherwise. The reply's
# next_fire_at (UTC) and next_fire_at_local say when it fires — read them rather
# than creating a test schedule and waiting.
lemma schedules create --agent triage-agent --cron "0 9 * * 1-5" --timezone Europe/Berlin --name weekday-triage
lemma schedules create --workflow nightly-review --cron "0 2 * * *"          # 02:00 UTC
lemma schedules create --workflow intake --at "2026-06-14T09:00:00Z"
lemma schedules create --agent POD_DEFAULT --name daily-digest --cron "0 9 * * 1-5" \
  --timezone America/New_York --instruction "Summarise yesterday's tickets and message me."

# DATASTORE — table row events; --on is REQUIRED (insert | update | delete | all)
lemma schedules create --workflow ticket-intake --datastore tickets --on insert --on update
lemma schedules create --workflow ticket-intake --datastore tickets --on all   # insert+update+delete

# WEBHOOK on an AGENT — the trigger id is what picks the app.
# --webhook-source names the inbound ENDPOINT (composio | github), not the app.
# (find the trigger id via `lemma connectors triggers list gmail -q "new email"`)
lemma schedules create --agent mail-triage \
  --webhook-source composio --connector-trigger gmail:composio:new_message --connector gmail

# WEBHOOK on a WORKFLOW — the trigger comes from the workflow's EVENT start.
# Passing --connector-trigger here is rejected.
lemma schedules create --workflow ticket-intake \
  --webhook-source composio --connector gmail
```

Bundle JSON (`schedules/<name>/<name>.json`) — `name` is the stable upsert key:

```json
{
  "name": "nightly-review",
  "schedule_type": "TIME",
  "config": { "cron": "0 2 * * *" },
  "workflow_name": "nightly-review",
  "is_active": true
}
```

**Config shape per type:**

- `TIME` — `{"cron": "0 2 * * *"}` (5-field cron) **or** `{"scheduled_at":
  "2026-06-14T09:00:00Z"}` (one-shot), plus an optional **`timezone`**.

  **`timezone` is an IANA zone name** (`"Europe/Berlin"`, `"America/New_York"`) and
  it is the zone the cron expression — or a `scheduled_at` with no offset — is read
  in. **Omit it and the schedule is UTC**, which is what every schedule written
  before the field existed already meant, so absence and `"UTC"` behave identically
  and nothing has to be rewritten to gain it. Set it whenever "9am" means 9am
  *somewhere*: `{"cron": "0 9 * * 1-5", "timezone": "Europe/Berlin"}` fires at 08:00Z
  in summer and 07:00Z in winter, which a UTC cron cannot do. An unknown zone is
  rejected at write time. On the CLI it is `--timezone` (alias `--tz`).

  Daylight saving is settled at write time, not left to the caller: on a
  spring-forward date a `0 2 * * *` schedule still fires (at the instant 02:00 would
  have been, which reads locally as 03:00) rather than being skipped, and on a
  fall-back date a daily schedule fires **once**, on the first of the two 01:00s.
- `DATASTORE` — `{"table_name": "tickets", "operations": ["INSERT", "UPDATE"]}`.
  `operations` is **required and explicit** — each must be `INSERT`, `UPDATE`, or
  `DELETE` (`--on all` expands to all three). A datastore schedule without
  operations is rejected. Add an optional `when` block to match on column values
  (see *Match conditions*).
- `WEBHOOK` — a `pod_connector` (the pod connector's slug) plus a connector
  trigger, and the trigger id is
  **kind-qualified** (`{app}:{kind}:{slug}`, e.g. `gmail:composio:new_message`);
  get it from `lemma connectors triggers list <connector-or-install>` (a catalog
  id such as `gmail` resolves to the org's default install; see `connectors.md`).

  **`source` is not the connector.** It names the **inbound webhook endpoint**
  (`POST /webhooks/{source}`), and this deployment answers on exactly two:
  **`composio`** and **`github`**. On a schedule that is bound to a pod connector *and* a
  connector trigger — every schedule you create the normal way — `source` is
  **decorative**: provisioning writes the real routing key (a Composio
  `provider_trigger_id`, or GitHub's `{installation_id, event}` — which overwrites
  `source` with `"github"` anyway), and nothing ever reads what you typed. So
  `--webhook-source gmail` is a label, not a subscription: the *trigger id* is what
  makes it Gmail.

  On an **unprovisioned** schedule — one whose stored config *is* the whole routing
  key, with no connector and trigger to bind — `source` is load-bearing, and a name the
  registry does not know is **refused at create**: *"No webhook source named 'x'
  delivers to this deployment, so a schedule listening for it could never fire."*
  That refusal is the reason to stop thinking of `source` as "which app": the useful
  values are the two endpoint names, and everything else is chosen by the trigger id.

  **Where the trigger id goes depends on the target**, and getting it wrong is a
  hard error, not a warning:
  - **Agent** webhook schedules carry `connector_trigger_id` themselves.
  - **Workflow** webhook schedules **derive** it from the workflow's `EVENT`
    start and **reject** a `connector_trigger_id` on the schedule. The workflow
    must have an `EVENT` start with a trigger id, or creation fails with
    *"Webhook workflow schedules require an EVENT workflow start"*.

  **A trigger may take parameters of its own**, declared by its `config_schema`
  and read with `lemma connectors triggers get <connector-or-install> <trigger-id>`. They
  go at the **top level** of `config`, alongside `source` — not nested under a
  `trigger_config` key, where nothing would match them. GitHub's
  `github:http:pull_request`, for example, takes an optional `repository_id` and
  `actions`; leaving both out means every repository and every action. Every one
  of these narrows a trigger that already works, so none is required.

  Some keys are **bound for you** and must not be written by hand. A GitHub
  schedule's `installation_id` comes from the account its pod connector
  resolves to when the schedule is created — it is not something to copy out of
  a URL, and a wrong one routes another organization's events at your pod.

  **Which account a schedule listens through is the connector's decision**, and
  it is made again on every use, as the schedule's **owner**: a per-member
  connector means the owner's own account, a shared connector the account its
  owner shared. Creating the schedule is your own use of the connector — the
  same checks as running one of its operations, with the same typed refusals
  (`CONNECTION_REQUIRED` with a `connect_url`, `CONNECTOR_NEEDS_ACCOUNT`, …).
  Afterwards, a delivery only fires the schedule while the connector still
  resolves: when the owner leaves the pod or a shared account is withdrawn it
  stops, and an MCP event schedule is turned off with the reason on it. Whoever
  changes what a schedule does becomes its owner, on their own use of the
  connector, and its subscription is made again as them.

  **A triggered agent needs its own connector grant.** Connecting the account
  authorizes the *person*; a scheduled run is a delegated workload and is
  refused unless the **agent** also holds `connector.use` on the connector
  (`lemma agents permissions add <agent> connector:<slug>:use`). Without it the run still
  starts and the repository is still bound — the machine simply has no
  credentials, and `git clone` fails with `could not read Username`.

Scaffold with `lemma schedules init <name>` (writes a commented TIME schedule, set
to `is_active: false` so it won't fire before its target exists).

## Event payloads — where the trigger data lands

The trigger populates the **`start`** namespace of a workflow run (manual runs have
no `start`). JMESPath expressions in workflow nodes reference it:

- `start.payload.*` — the event body.
  - **DATASTORE**: the **whole row** as it stands after the write — including
    columns this write never touched and defaults the database filled in. On a
    `DELETE` it is the row as it stood before removal.
  - **WEBHOOK**: the connector event payload (check the trigger's `payload_schema`
    via `lemma connectors triggers get`).
- `start.metadata.*` — event metadata. For DATASTORE this is **`table_name`,
  `record_id`, `operation`, `event_occurred_at`**, plus what the write did:
  - `changed` — the column names this `UPDATE` wrote (empty for INSERT/DELETE).
  - `previous` — those columns' prior values, and only those. Absent columns
    were not touched; an empty object means there was no prior row.

  ⚠️ The new row's **`record_id` is at `start.metadata.record_id`, NOT
  `start.payload`** — a common mistake. Bind your first node's input to
  `start.metadata.record_id`.

  Bulk writes and imports **do not fire per row by default**: `config.bulk_writes`
  and `config.import_writes` are `skip` unless set (the changes are recorded as
  skipped). Set either to `each` (one fire per matching row, same whole-row payload
  as a single write) or `batch` (one fire with the rows as a list, run as the
  schedule's owner). The pod setting `triggers.bulk_writes_default` changes the
  default for `bulk_writes`.
- `start.llm_output.*` — the filter's answers, if you set a filter (below).

For an **agent** target, the event is delivered as the message that wakes the agent
— write the instruction to read that message.

Debugging "it didn't fire": read telemetry before logs. `lemma schedules get <id>`
shows `last_fired_at`, `last_run_id`, `last_fire_status` (`TRIGGERED` / `FILTERED` /
`ERROR`), and `last_error`.

## Match conditions — the deterministic filter (DATASTORE only)

A `when` block on a `DATASTORE` config decides whether to fire **from the event
alone**: no database read, no model call, no cost. Reach for it first, and keep
`filter_instruction` for judgement a comparison cannot express.

```json
{
  "name": "on-approval",
  "schedule_type": "DATASTORE",
  "config": {
    "table_name": "tickets",
    "operations": ["UPDATE"],
    "when": { "status": { "to": "approved" }, "priority": "high" }
  },
  "workflow_name": "fulfil-ticket",
  "is_active": true
}
```

Keys are column names. A bare value is shorthand for `equals`, so
`{"priority": "high"}` and `{"priority": {"equals": "high"}}` are the same.
**Every condition must hold** — conditions AND together, and so do operators
within one column. There is no OR and no nesting; that is what the LLM filter is
for.

| Operator | Holds when | Needs |
| --- | --- | --- |
| `equals` / `not_equals` | the value after the write | any |
| `in` / `not_in` | value is (not) in the list | any |
| `to` | the value **became** this — it is this now and was not before | INSERT or UPDATE |
| `from` | the value **was** this before the write | UPDATE |
| `changed` | the value differs from before | UPDATE |
| `written` | the write set this column, even to the same value | UPDATE |

The distinction that matters: **`{"status": "approved"}` fires on every write
that leaves the row approved. `{"status": {"to": "approved"}}` fires only on the
write that made it approved.** Reaching for the first when you meant the second
is how a trigger ends up running on every subsequent edit.

Two rules follow from what each operation carries:
- An **INSERT** has nothing to have moved away from, so `changed`, `written` and
  `from` never hold. `to` does — a row created already approved *became*
  approved, so you do not need a second trigger for it.
- A **DELETE** carries the removed row but no prior image, so value operators
  work and change operators never hold.

A condition no declared operation could satisfy is **rejected at save time**
rather than leaving you with a trigger that silently never fires — so
`operations: ["INSERT"]` with `{"status": {"changed": true}}` is an error.

Filtered events record `last_fire_status: FILTERED`, same as the LLM filter.

On the CLI there is no dedicated flag — pass the block through `--data`, which
merges into the config built from `--datastore` / `--on`:

```bash
lemma schedules create --workflow fulfil-ticket --datastore tickets --on update \
  --data '{"config": {"when": {"status": {"to": "approved"}}}}'
```

## Event filtering — drop the noise

Chatty webhook/datastore sources fire constantly. A `filter_instruction` is a
**natural-language predicate judged per event before the target fires** — asked as
a *decision* (see `functions.md` → Decisions), on whichever provider the
deployment chose. An event it says no to is **skipped**: recorded as a run with
status `FILTERED` carrying the answer, and the schedule's `last_fire_status`
becomes `FILTERED`. An event the filter **cannot tell** about is skipped too.

`filter_output_schema` asks extra **closed** questions alongside the yes/no, and
their answers reach the target at `start.llm_output.*`:

```json
"filter_output_schema": {
  "type": "object",
  "properties": {
    "category": { "type": "string", "enum": ["bug", "billing", "other"], "description": "What is it about?" },
    "urgent":   { "type": "boolean", "description": "Does it need a reply today?" }
  }
}
```

- Allowed: a string `enum` (or `oneOf` of `{const, description}`), an array of one
  with `uniqueItems`, `boolean`, or `integer` with `minimum`/`maximum` (2-11
  levels). **Free text and open-ended numbers are not asked** — such fields are
  left out and logged; there is no `reason` field any more.
- `start.llm_output` is `{ should_proceed: true, <your answers>, _decision:
  { provider, model, unsure, confidence: {question: number|null} } }`.
- If no decision provider answers, the event is retried with backoff and then
  recorded **`DEAD_LETTERED`** (`ScheduleFilterUnavailable`) — never sent past the
  filter. A person can retry it by hand (`lemma schedules runs retry`).
- **Not on a `TIME` schedule**: it fires on the clock, so there is no event to
  judge, and a filter there is refused (422).
- History: `lemma schedules runs list <schedule> --no-skipped` shows the firings
  only; `--skipped` shows only the skips (`?skipped=` on
  `GET /pods/{pod}/schedules/{id}/runs`).

> On a `DATASTORE` trigger, prefer a `when` block for anything a comparison can
> decide. The filter costs a model call **per event**; a `when` block costs
> nothing and runs first, so events it rules out never reach the model.

```json
{
  "name": "important-mail",
  "schedule_type": "WEBHOOK",
  "config": { "source": "composio" },
  "pod_connector": "gmail",
  "workflow_name": "ticket-intake",
  "filter_instruction": "Only process emails from external customers describing a problem or request. Ignore newsletters, receipts, and internal mail.",
  "is_active": true
}
```

No `connector_trigger_id` here: this targets a **workflow**, so the id comes from
`ticket-intake`'s `EVENT` start. On an **agent** webhook schedule you would add
`"connector_trigger_id": "gmail:composio:new_message"` instead — and *that* is what
makes this a Gmail schedule. `source` is the inbound endpoint (`composio` here,
`github` for a GitHub App trigger), not the app; on a bound schedule like this one it
is overwritten or ignored by provisioning either way.

On the CLI: `--filter "<predicate>"`.

## Patterns

- **Cron report agent** — `TIME` → agent that queries tables and posts/uploads a
  summary.
- **Row-driven process** — `DATASTORE` on `INSERT` → workflow that enriches/triages
  the new record (bind to `start.metadata.record_id`).
- **Reactive choreography** — workload A writes a row, a `DATASTORE` schedule on that
  table fires, workload B reacts and writes elsewhere. Chaining these keeps complex
  pods simple — each workload does one thing (pod-model heuristic #4) — but mind the
  **Trigger loops** and **Datastore bursts** gotchas below: never let B write back to
  A's table on the same operation, and throttle bulk writers.
- **Inbound-email pipeline** — `WEBHOOK` gmail trigger + `filter_instruction` →
  intake workflow.
- **SLA sweeper** — `TIME` hourly → workflow that queries overdue records and
  assigns exception FORMs.

## Manage

```bash
lemma schedules list [--type TIME|DATASTORE|WEBHOOK] [--agent X] [--workflow Y] [--active]
lemma schedules get <id-or-name>
lemma schedules pause <id-or-name>      # stop firing without deleting
lemma schedules resume <id-or-name>
lemma schedules update <id> --data '{"filter_instruction": "..."}'
lemma schedules delete <id> --yes
```

## Trigger or wait?

Both make something happen later, so be precise about which you want:

> **A trigger starts work nobody was doing. A wait resumes work already underway.
> If there is no conversation sitting there waiting for it, you want a trigger.**

| You want | Use |
| --- | --- |
| "When an invoice is approved, run the payout workflow" | `DATASTORE` trigger — no one is waiting; this is a standing rule |
| "Every weekday at 9, post the summary" | `TIME` trigger |
| "Wait for this build to finish" | `wait_for(process_id=...)` |
| "Give it ten minutes, then look" | `wait_for(seconds=600)` |
| "I filed the invoice; finish once it's approved" | Ask the user and end the turn, or wait and re-check on wake |

**Reacting to a row changing is always a trigger.** `wait_for` waits on a time, a
process on the agent's machine or a sub-agent run — it deliberately has no record-watching mode,
so there is exactly one way to react to data changing and it is a trigger.

They differ in kind, not just scope. A trigger is a pod resource: it persists, it
is listed, it can be paused, and it fires any number of times. A wait is one
suspended execution — invisible in `schedules`, resolves exactly once, and caps at
24h.

## Limits & gotchas

- **Don't create throwaway test schedules.** To check *when* a schedule fires,
  read `next_fire_at` / `next_fire_at_local` from the create reply. To check
  *what* it does, run the target directly (`lemma workflows run`, or do the
  agent's instruction in your own conversation). If you did make one, delete it
  — `lemma schedules delete <name> --yes`; an agent may delete a schedule it
  created itself without asking for approval.
- **Trigger loops.** If a `DATASTORE` schedule fires on `UPDATE` of a table and its
  target workflow **writes that same table**, each write re-fires the schedule —
  a loop that runs until the daily fire cap pauses the schedule
  (`paused_reason: fire_cap`, and its owner is told). Write to a different table,
  fire only on `INSERT`, or guard the write so it's a no-op when nothing changed.
- **Datastore bursts.** Single-row writes fire **per matching row operation** — a
  loop of 500 `records create` calls means 500 runs. Bulk writes and imports are
  skipped unless `bulk_writes` / `import_writes` says `each` or `batch`; prefer
  `batch` over `each` for a large writer.
- **Concurrency.** Firings are not serialized by default — overlapping runs of the
  same target can be in flight at once. Make targets idempotent; don't assume the
  previous run finished. To cap it, set `max_concurrent_runs` (1–1000) in the
  schedule's `config`: a fire that finds it full is **skipped** (recorded as
  skipped), not queued.
- **Webhook prerequisites.** A WEBHOOK schedule needs a pod connector declared
  in the pod, and an account it resolves to for you — your own connected for a
  per-member one, a shared one for a shared one (`connectors.md`) — plus a
  publicly reachable backend to receive the POST (local stacks may not get
  webhook deliveries).

## Verify

```bash
lemma schedules get <name>                  # active? right target? right config?
                                            # TIME: next_fire_at (UTC) / next_fire_at_local

# TIME: don't wait for a fire — run the target once directly instead
lemma workflows run <workflow> --data '{}'  # or do the agent's instruction yourself
lemma workflows runs list <workflow>        # after a real fire: lemma schedules runs list <name>

# DATASTORE: create a test row, confirm a run started with the row in start.payload
lemma records create tickets --data '{"title":"smoke","status":"new"}'
lemma workflows runs get <run-id>           # confirm start.metadata.record_id is populated
```

## See also

- The model → `pod-model.md` · trigger ids & accounts → `connectors.md`
- Workflow run context (`start.*`, JMESPath) → `workflows.md`
- Designing what fires when → `pod-design.md` · operate → the `lemma-user` skill
