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
- **`WEBHOOK`** — a connector event (new email, message posted). Needs a connected
  account and a kind-qualified trigger id. *"When mail arrives, intake it."*

## Whose identity does a fired run use?

This decides which rows the run can see, so it is the first thing to settle —
and it is not always the schedule's creator:

| Trigger | Runs as |
| --- | --- |
| `TIME` | the schedule's configured user |
| `WEBHOOK` | the schedule's configured user |
| `DATASTORE` on an **RLS** table | the **owner of the row that changed** |
| `DATASTORE` on a **shared** table | the schedule's configured user |

The RLS row-owner rule is the useful one: each member's write starts a run scoped
to **that member**, over their own rows, with their `/me` and their connected
accounts. That is what makes "write a row, get agentic work done as yourself" a
one-line app feature — see `app-recipes/rls-table.md`. A datastore fire that
carries no owner is treated as an error rather than quietly falling back.

Creating a `DATASTORE` schedule needs **`datastore.table.update` on the watched
table**, not just permission to write records to it.

The triggering event becomes the run's **start payload**. Design the first workflow
node (or the agent's instruction) around that exact shape — see *Event payloads*.

**Target choice** (mirrors `pod-design.md`):
- → **agent** when each firing needs judgment over current state ("review stale
  tickets and nudge owners").
- → **workflow** when each firing runs the same multi-step process ("nightly: load
  batch, loop, write report").

> **Server-side, not live UI.** A `DATASTORE` schedule reacts to a row change by
> *doing work* (starting a workload). To keep an **app's UI** fresh on row changes, use
> `datastore.watchChanges` (a client-side WebSocket) instead — see `apps.md`. Don't
> reach for a schedule when you only need the screen to update.

## Create a schedule

CLI flags cover the common cases. Exactly one of `--agent` / `--workflow` is
required:

```bash
# TIME — cron (5-field) or one-shot ISO timestamp
lemma schedules create --agent triage-agent --cron "0 9 * * 1-5" --name weekday-triage
lemma schedules create --workflow nightly-review --cron "0 2 * * *"
lemma schedules create --workflow intake --at "2026-06-14T09:00:00Z"

# DATASTORE — table row events; --on is REQUIRED (insert | update | delete | all)
lemma schedules create --workflow ticket-intake --datastore tickets --on insert --on update
lemma schedules create --workflow ticket-intake --datastore tickets --on all   # insert+update+delete

# WEBHOOK on an AGENT — carries the trigger id
# (find it via `lemma connectors triggers list <auth-config>`)
lemma schedules create --agent mail-triage \
  --webhook-source gmail --connector-trigger gmail:composio:new_message --account <account-id>

# WEBHOOK on a WORKFLOW — the trigger comes from the workflow's EVENT start.
# Passing --connector-trigger here is rejected.
lemma schedules create --workflow ticket-intake \
  --webhook-source gmail --account <account-id>
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
  "2026-06-14T09:00:00Z"}` (one-shot).
- `DATASTORE` — `{"table_name": "tickets", "operations": ["INSERT", "UPDATE"]}`.
  `operations` is **required and explicit** — each must be `INSERT`, `UPDATE`, or
  `DELETE` (`--on all` expands to all three). A datastore schedule without
  operations is rejected. Add an optional `when` block to match on column values
  (see *Match conditions*).
- `WEBHOOK` — `{"source": "<app>"}` plus `account_id`. The trigger id is
  **kind-qualified** (`{app}:{kind}:{slug}`, e.g. `gmail:composio:new_message`);
  get it from `lemma connectors triggers list <auth-config>` (see
  `connectors.md`).

  **Where the trigger id goes depends on the target**, and getting it wrong is a
  hard error, not a warning:
  - **Agent** webhook schedules carry `connector_trigger_id` themselves.
  - **Workflow** webhook schedules **derive** it from the workflow's `EVENT`
    start and **reject** a `connector_trigger_id` on the schedule. The workflow
    must have an `EVENT` start with a trigger id, or creation fails with
    *"Webhook workflow schedules require an EVENT workflow start"*.

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

  ⚠️ **Bulk writes are the exception**: `bulk_create_records` /
  `bulk_upsert_records` emit a payload of only the submitted columns, not the
  stored row, so a condition on a database-defaulted column will not match rows
  inserted that way.
- `start.llm_output.*` — the structured output of the LLM filter, if you set one
  (below).

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

## LLM event filtering — drop the noise

Chatty webhook/datastore sources fire constantly. A `filter_instruction` is a
**natural-language predicate evaluated per event before the target fires**; events
that fail it are dropped (status `FILTERED`, not `TRIGGERED`). Add an optional
`filter_output_schema` to capture structured output the run can read at
`start.llm_output.*`.

> On a `DATASTORE` trigger, prefer a `when` block for anything a comparison can
> decide. The filter costs a model call **per event**; a `when` block costs
> nothing and runs first, so events it rules out never reach the model.

```json
{
  "name": "important-mail",
  "schedule_type": "WEBHOOK",
  "config": { "source": "gmail" },
  "account_id": "${gmail_account}",
  "workflow_name": "ticket-intake",
  "filter_instruction": "Only process emails from external customers describing a problem or request. Ignore newsletters, receipts, and internal mail.",
  "is_active": true
}
```

No `connector_trigger_id` here: this targets a **workflow**, so the id comes from
`ticket-intake`'s `EVENT` start. On an **agent** webhook schedule you would add
`"connector_trigger_id": "gmail:composio:new_message"` instead.

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

## Trigger or snooze?

Both make something happen later, so be precise about which you want:

> **A trigger starts work nobody was doing. A snooze resumes work already underway.
> If there is no conversation sitting there waiting for it, you want a trigger.**

| You want | Use |
| --- | --- |
| "When an invoice is approved, run the payout workflow" | `DATASTORE` trigger — no one is waiting; this is a standing rule |
| "Every weekday at 9, post the summary" | `TIME` trigger |
| "Give the build ten minutes, then check it" | `snooze(seconds=600)` |
| "I filed the invoice; finish once it's approved" | Ask the user and end the turn, or snooze and re-check on wake |

**Reacting to a row changing is always a trigger.** `snooze` is time-based only —
it deliberately has no record-watching mode, so there is exactly one way to react
to data changing and it is a trigger.

They differ in kind, not just scope. A trigger is a pod resource: it persists, it
is listed, it can be paused, and it fires any number of times. A snooze is one
suspended execution — invisible in `schedules`, resolves exactly once, and caps at
24h.

## Limits & gotchas

- **Pause or delete test schedules.** A near-future cron you set to verify keeps
  firing after you move on — `lemma schedules pause` it the moment you're done.
- **Trigger loops.** If a `DATASTORE` schedule fires on `UPDATE` of a table and its
  target workflow **writes that same table**, each write re-fires the schedule —
  an infinite loop. Write to a different table, fire only on `INSERT`, or guard the
  write so it's a no-op when nothing changed.
- **Datastore bursts.** Datastore schedules fire **per matching row operation** — a
  bulk insert of 500 rows means 500 runs. Throttle the writer or fire on a coarser
  signal.
- **Concurrency.** Firings are not serialized — overlapping runs of the same target
  can be in flight at once. Make targets idempotent; don't assume the previous run
  finished.
- **Webhook prerequisites.** A WEBHOOK schedule needs the connector account
  connected first (`connectors.md`) and a publicly reachable backend to receive the
  POST (local stacks may not get webhook deliveries).
- **Internal schedules.** Workflow `WAIT_UNTIL` nodes create their own schedules
  that show up in `list` — don't touch them.

## Verify

```bash
lemma schedules get <name>                  # active? right target? right config?

# TIME: set a near-future cron / --at, wait, then check the run, then PAUSE it
lemma workflows runs list <workflow>        # or: lemma conversations list --agent <agent>

# DATASTORE: create a test row, confirm a run started with the row in start.payload
lemma records create tickets --data '{"title":"smoke","status":"new"}'
lemma workflows runs get <run-id>           # confirm start.metadata.record_id is populated
```

## See also

- The model → `pod-model.md` · trigger ids & accounts → `connectors.md`
- Workflow run context (`start.*`, JMESPath) → `workflows.md`
- Designing what fires when → `pod-design.md` · operate → the `lemma-user` skill
