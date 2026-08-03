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
  account and a provider-qualified trigger id. *"When mail arrives, intake it."*

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

# WEBHOOK — connector trigger (find the id via `lemma connectors triggers list <auth-config>`)
lemma schedules create --workflow ticket-intake \
  --webhook-source gmail --connector-trigger gmail:composio:new_message --account <account-id>
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
  operations is rejected.
- `WEBHOOK` — `{"source": "<app>"}` plus `connector_trigger_id` and `account_id`
  fields. The trigger id is **provider-qualified** (`{app}:{provider}:{slug}`, e.g.
  `gmail:composio:new_message`); get it from `lemma connectors triggers list
  <auth-config>` (see `connectors.md`).

Scaffold with `lemma schedules init <name>` (writes a commented TIME schedule, set
to `is_active: false` so it won't fire before its target exists).

## Event payloads — where the trigger data lands

The trigger populates the **`start`** namespace of a workflow run (manual runs have
no `start`). JMESPath expressions in workflow nodes reference it:

- `start.payload.*` — the event body.
  - **DATASTORE**: the affected row's fields.
  - **WEBHOOK**: the connector event payload (check the trigger's `payload_schema`
    via `lemma connectors triggers get`).
- `start.metadata.*` — event metadata. For DATASTORE this is **`table_name`,
  `record_id`, `operation`, `event_occurred_at`**. ⚠️ The new row's
  **`record_id` is at `start.metadata.record_id`, NOT `start.payload`** — a common
  mistake. Bind your first node's input to `start.metadata.record_id`.
- `start.llm_output.*` — the structured output of the LLM filter, if you set one
  (below).

For an **agent** target, the event is delivered as the message that wakes the agent
— write the instruction to read that message.

Debugging "it didn't fire": read telemetry before logs. `lemma schedules get <id>`
shows `last_fired_at`, `last_run_id`, `last_fire_status` (`TRIGGERED` / `FILTERED` /
`ERROR`), and `last_error`.

## LLM event filtering — drop the noise

Chatty webhook/datastore sources fire constantly. A `filter_instruction` is a
**natural-language predicate evaluated per event before the target fires**; events
that fail it are dropped (status `FILTERED`, not `TRIGGERED`). Add an optional
`filter_output_schema` to capture structured output the run can read at
`start.llm_output.*`.

```json
{
  "name": "important-mail",
  "schedule_type": "WEBHOOK",
  "config": { "source": "gmail" },
  "connector_trigger_id": "gmail:composio:new_message",
  "account_id": "<account-id>",
  "workflow_name": "ticket-intake",
  "filter_instruction": "Only process emails from external customers describing a problem or request. Ignore newsletters, receipts, and internal mail.",
  "is_active": true
}
```

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
