# Workflows

Workflows are resumable playbooks that orchestrate **functions, agents, and humans**
in one graph. This is the pod's process layer and its human-agent collaboration
layer: FORM nodes are assigned to pod members, so a single run can move work between
deterministic code, LLM judgment, and human decisions — with durable state and
history throughout. Where an agent *decides at runtime* who to call, a workflow is the
**fixed graph you control**: deterministic order, durable run context, retries.

Reach for a workflow when humans need to **see progress, approve, or be assigned
steps**, or when code + agents + humans interleave in a durable, observable sequence
(pod-model heuristics #2, #5) — not for a lone function call. And keep the graph
minimal: one `AGENT` node should do a whole cohesive judgment and return rich
`output_schema`, never be the first link of an agent→agent chain (heuristic #1).

> Grounds in `pod-model.md` (the automation layer — the process tier above functions
> and agents). This is the build + CLI view; the `lemma-user` skill is the operator
> view of starting runs and clearing form queues.

## The model, for workflows

A workflow is a graph; a **run** is one execution with a durable context. The pod-model
rules that matter here are about *whose identity each step runs under* and *how that
scopes data*:

- **Every run has one delegated identity.** Who that is depends on how the run
  started:
  - `workflows run` — the human who called it.
  - A **`TIME`** or **`WEBHOOK`** schedule — the schedule's configured user.
  - A **`DATASTORE`** schedule on an **RLS** table — the **owner of the row that
    changed**, not the schedule's creator. (A datastore fire that somehow carries
    no owner is an error, not a fallback.)
  - A **`DATASTORE`** schedule on a **shared** table — no row has an owner there,
    so it falls back to the schedule's creator.

  **`FUNCTION` and `AGENT` nodes run as that identity** — they are workloads
  delegated to the run owner, exactly like a directly invoked function or agent.
  So on an RLS table a node reads only the **run owner's** rows and stamps writes
  with **their** id; `/me` is the run owner's tree; a connector call goes through
  the run owner's connected account. A `FUNCTION`/`AGENT` node never sees more
  rows than the run owner would.

  Plan RLS around both directions of that: a workflow that must read *all*
  members' rows needs **shared** tables (see `tables.md`) — but a workflow that
  should run *as each member, over their own rows* wants an **RLS** table plus a
  `DATASTORE` trigger, which is the per-user agentic pattern in
  `app-recipes/rls-table.md`.
- **Zero access by default still applies — per node's workload.** A `FUNCTION` node's
  function and an `AGENT` node's agent each need their **own** name-based grants for
  the tables/files/connectors they touch; the workflow does not lend them access. A
  missing grant surfaces as `MISSING_WORKLOAD_RESOURCE_GRANT` on that node. Grant
  every callee, then wire it into the graph.
- **`FORM` nodes are the human tier.** A FORM is assigned to a specific **pod member**
  and waits for *their* submission — gated by pod membership/role, not by workload
  grants. This is how a run hands a decision to a person.

So routing/data flow is governed by JMESPath over the run context (below), but *who
can read/write what* at each node is still the delegated-identity + zero-default-grant
model from `pod-model.md`.

> Scaffold a working FORM→END graph with `lemma workflows init intake`, then extend
> it. Run `lemma workflows validate ./my-pod/workflows/intake` (entry node, edge
> endpoints, END, agent/function targets) before importing.

## Core Concepts

- **Definition**: named graph — `nodes`, `edges`, `start`, description. Graphs are validated at save time (single entry node, no dangling edges/rule targets, reachable nodes, compilable expressions); invalid graphs are rejected with the full issue list.
- **Run**: one execution. Carries status, current node, run context, step history, and `active_wait` (what it's waiting on) when status is `WAITING`.
- **Runs have no arbitrary start input object.** `workflows run` creates the run; if the entry node is a FORM, `--data` submits that entry form. Trigger payloads (schedule/event/datastore) are injected by the platform under `start.*`.
- **Run context** (what every expression resolves against):
  - `<node_id>.<field>` — output of any executed node (form fields, agent output, function result)
  - `start.payload.*`, `start.metadata.*`, `start.llm_output.*` — trigger data, only on triggered runs (manual runs have no `start`)
  - `loop.item`, `loop.index`, `loop.count`, `loop.<item_var>` — current loop iteration, only inside loop bodies
- **One expression dialect**: everything is **JMESPath** — input mappings, decision conditions, loop `items_path`, assignee expressions.
- **Missing paths fail loudly**: a required input mapping that resolves to nothing FAILS the run naming the path (use `"optional": true` or a literal binding for genuinely optional values). Decision conditions treat missing paths as falsy — they probe, mappings demand.
- **Waits**: FORM waits for a pod member's submission; AGENT waits for an agent conversation; FUNCTION can wait on async runs; WAIT_UNTIL waits on a timer. The run's `active_wait` shows the type, node, assignee, and external reference.

Start types: `MANUAL`, `SCHEDULED`, `DATASTORE_EVENT`, `EVENT`. Create schedules (see `schedules-and-triggers.md`) for non-manual starts; keep the graph focused on what happens after `start.payload` arrives.

**Triggered starts require a `start.config` (the server 422s without it; `lemma workflows validate` now flags it).** `MANUAL` takes no config; every non-manual type needs a typed config:

- `SCHEDULED` → `config.schedule_type` ∈ `ONCE | CRON`.
- `DATASTORE_EVENT` → `config.table_name` (required), plus optional `config.operations` (a subset of `INSERT | UPDATE | DELETE`; omit to match all).
- `EVENT` → `config.connector_trigger_id` **and** `config.connector_id`.

```json
"start": { "type": "DATASTORE_EVENT", "config": { "table_name": "tickets", "operations": ["INSERT"] } }
```

**DATASTORE_EVENT trigger data — the changed record's id is at `start.metadata.record_id`, NOT `start.payload.*`.** The record's column values land in `start.payload.*`; the trigger's `record_id`, `table_name`, `operation`, and `event_occurred_at` land in `start.metadata.*`. Mapping `start.payload.id` (or `start.payload.record_id`) silently resolves to null — this footgun nulled out the record id for agents. Always read the id as `{ "type": "expression", "value": "start.metadata.record_id" }`.

## Node Types

### FORM — the human step

Pauses the run until the assigned pod member submits the form. Use for intake, review, approval, missing-data collection, QA, and exceptions. A FORM as the **entry node** is how manual workflows collect their input.

```json
{
  "id": "manager_review",
  "type": "FORM",
  "label": "Manager review",
  "config": {
    "assignee_pod_member_id_expression": "intake.manager_pod_member_id",
    "input_schema": {
      "type": "object",
      "properties": {
        "approved": { "type": "boolean" },
        "notes": { "type": "string" }
      },
      "required": ["approved"]
    },
    "ui_schema": { "ui:order": ["approved", "notes"] }
  }
}
```

Config: `input_schema` (JSON Schema for the submission), optional `ui_schema` (renderer hints), `assignee_pod_member_id` (fixed member UUID) or `assignee_pod_member_id_expression` (JMESPath resolving to a member id; takes precedence). With no assignee, any pod member with execute access can submit.

The active wait carries the form schema, so UIs render straight from the run response. Submitted fields become the node's output: `manager_review.approved`.

**Dynamic schema** — `input_schema`/`ui_schema` may embed the **same typed bindings** as `input_mapping` (`{"type": "expression", "value": "<jmespath>"}` / `{"type": "literal", "value": ...}`), resolved against the run context (`<node_id>.<field>`, `start.*`, `loop.*`) when the form suspends; the resolved schema is what the UI renders. One binding syntax across the whole workflow. Use it to populate a dropdown from a prior node, or pre-fill an editable field:

```json
"input_schema": {
  "type": "object",
  "properties": {
    "category": { "type": "string", "enum": { "type": "expression", "value": "categorize.labels" } },
    "body":     { "type": "string", "default": { "type": "expression", "value": "draft_email.body", "optional": true } }
  },
  "required": ["category"]
}
```

`optional` controls failure as in `input_mapping`: a required binding (default) that resolves to nothing fails the node; mark `default` sources `optional: true`. A resolved `enum` must be a non-empty list. On submit, inputs are validated against the resolved schema (dynamic enums enforced, `422` on a bad value) and omitted fields fall back to their `default`. (`expression`/`literal` aren't valid JSON Schema types, so a binding is never confused with a real subschema.)

Design notes: keep forms small and decision-oriented; assign to pod members (not generic users) so permissions and the `lemma workflows runs waiting` inbox stay consistent; for approvals capture the boolean **and** a reason; durable business state still belongs in tables.

### AGENT — the judgment step

```json
{
  "id": "parse_request",
  "type": "AGENT",
  "label": "Parse request",
  "config": {
    "agent_name": "request-parser",
    "input_mapping": {
      "raw": { "type": "expression", "value": "intake.raw" },
      "channel": { "type": "literal", "value": "support" }
    }
  }
}
```

Create and test the agent first; give it an `output_schema` when downstream nodes consume the result. While the agent works the run is `WAITING` with `active_wait.wait_type == "AGENT"` and the conversation id in `active_wait.external_ref`. A periodic sweep recovers runs whose completion events were lost.

The agent runs **as the run's owner** (delegated identity) and under **its own
grants** — so its `POD` reads/writes are RLS-scoped to the run owner, and the agent
needs explicit grants for every table/file/connector it touches or the node fails with
`MISSING_WORKLOAD_RESOURCE_GRANT`. Grant + test the agent standalone before wiring it
in (see `agents.md`).

### FUNCTION — the deterministic step

```json
{
  "id": "record_approval",
  "type": "FUNCTION",
  "label": "Record approval",
  "config": {
    "function_name": "record_approval",
    "input_mapping": {
      "request_id": { "type": "expression", "value": "intake.request_id" },
      "approved": { "type": "expression", "value": "manager_review.approved" },
      "notes": { "type": "expression", "value": "manager_review.notes", "optional": true }
    }
  }
}
```

Use for validation, calculations, reliable writes, external API calls. A FUNCTION node dispatches **asynchronously for `API` and `JOB` alike** and suspends on a FUNCTION wait until the run finishes, so the node's own type doesn't change how the workflow waits. Like the AGENT node, a FUNCTION node runs **as the run's owner** with **the function's own grants** — RLS-scoped to that user; grant every table/file/connector the function touches (see `functions.md`).

### DECISION — branching

Branch conditions live in `config.rules`, **not on edges**. Edge `condition` fields are never evaluated.

```json
{
  "id": "approval_route",
  "type": "DECISION",
  "label": "Approved?",
  "config": {
    "rules": [
      { "condition": "manager_review.approved == `true`", "next_node_id": "draft_response" }
    ]
  }
}
```

Rules are evaluated in order; the first truthy condition wins and execution jumps to its `next_node_id`. **When no rule matches, execution follows the decision node's FIRST-listed outgoing edge — that edge is your default/else branch.**

**Default-edge footgun (`validate` now warns):** because the else branch is just the first outgoing edge, if that default edge targets the *same node a rule also targets*, an input that matches no rule is silently routed as if the rule had fired — e.g. a rejection that matches no rule falls through to the approval node and behaves like an approval. No error is raised. The fix: give each branch its own explicit edge to a distinct node, and order the edges so the **else-edge is listed first**. Cover the remaining case with an explicit rule (or point the default at a dedicated "neither" handler) rather than letting it share a target with a rule.

Conditions are **JMESPath** (same dialect as input mappings):

```text
# Good
manager_review.approved == `true`
parse_email.score >= `80`
parse_email.category == 'invoice'
contains(parse_email.tags, 'urgent')
length(parse_email.items) > `0`

# Bad — these are wrong
manager_review.approved == true     # bare true is not JMESPath; use `true` (backticks)
manager_review.approved == True     # Python syntax; conditions are JMESPath now
edge.condition == 'approved'        # edge conditions are not evaluated
```

Truthiness: `null`, `false`, `""`, `[]`, `{}` are falsy; everything else (including `` `0` ``) is truthy. Missing paths are falsy. Conditions are compile-checked at graph save, so typos in syntax are caught before any run.

### LOOP — iterate an array

```json
{
  "id": "line_loop",
  "type": "LOOP",
  "label": "Each line item",
  "config": {
    "items_path": "parse_request.items",
    "item_var_name": "line",
    "child_node_id": "record_line"
  }
}
```

Inside the body, read the current item from the reserved `loop` namespace: `loop.line` (or always `loop.item`), index as `loop.index`, total as `loop.count`. Wire an edge from the body's last node back to the loop node. When all items are done, the loop node's output is `{ "results": [<body output per item>], "count": <n> }` — downstream nodes read `line_loop.results`.

### WAIT_UNTIL — timer

```json
{ "id": "follow_up_delay", "type": "WAIT_UNTIL", "label": "Tomorrow",
  "config": { "timeout_seconds": 86400 } }
```

Use for cooldowns, SLA holds, follow-up gaps. Suspends on a TIME wait (`active_wait.payload.scheduled_at` shows when it fires).

### END

```json
{ "id": "end", "type": "END", "label": "Done" }
```

Multiple END nodes are fine when it makes the graph readable. END nodes cannot have outgoing edges.

## Input Mapping

```json
{
  "request_id": { "type": "expression", "value": "intake.request_id" },
  "memo": { "type": "expression", "value": "intake.memo", "optional": true },
  "queue": { "type": "literal", "value": "priority-review" }
}
```

Common paths: `start.payload.<field>` (triggered runs), `<form_node_id>.<field>`, `<agent_node_id>.<field>`, `<function_node_id>.<field>`, `loop.<item_var>.<field>` (inside loop bodies). Plain strings are not auto-treated as expressions — always use the `{type, value}` binding object. Required expressions that resolve to nothing fail the run with the path named in the error.

## Human-Agent Collaboration Patterns

- **Approval gate**: `FUNCTION prepare → FORM approve → DECISION → FUNCTION commit / END`. The classic; humans approve what code prepared.
- **Agent-assisted review**: `FORM intake → AGENT analyze → FORM reviewer_decision (sees agent output) → FUNCTION save`. The agent does the heavy reading; the human decides.
- **Escalate on low confidence**: ``AGENT classify → DECISION (classify.confidence >= `0.8`?) → auto path / FORM human_classify``. Humans handle only the uncertain tail.
- **Exception routing**: `FUNCTION validate → DECISION → FORM fix_data → FUNCTION retry`. Failures become assigned work instead of dead runs.
- **Timed follow-up**: `FORM decision → WAIT_UNTIL → AGENT draft_follow_up → FORM send_review`.
- **Batch with human sampling**: schedule starts workflow → `FUNCTION load_batch → LOOP → AGENT process`, decision inside the loop assigns FORMs only for flagged items.

## Complete Example

Intake form → agent parse → second-reviewer approval → branch → per-item function writes → cooldown → end:

```json
{
  "name": "assigned-review",
  "start": { "type": "MANUAL" },
  "nodes": [
    { "id": "intake", "type": "FORM", "label": "Reviewer A intake",
      "config": {
        "assignee_pod_member_id": "${reviewer}",
        "input_schema": { "type": "object",
          "properties": { "raw": { "type": "string" } }, "required": ["raw"] } } },
    { "id": "parse", "type": "AGENT", "label": "Parse input",
      "config": { "agent_name": "review-parser",
        "input_mapping": { "raw": { "type": "expression", "value": "intake.raw" } } } },
    { "id": "approval", "type": "FORM", "label": "Reviewer B approval",
      "config": {
        "assignee_pod_member_id": "POD_MEMBER_ID_B",
        "input_schema": { "type": "object",
          "properties": { "approved": { "type": "boolean" } }, "required": ["approved"] } } },
    { "id": "approved_route", "type": "DECISION", "label": "Approved?",
      "config": { "rules": [
        { "condition": "approval.approved == `true`", "next_node_id": "line_loop" } ] } },
    { "id": "line_loop", "type": "LOOP", "label": "Each line item",
      "config": { "items_path": "parse.items", "item_var_name": "line",
        "child_node_id": "record_line" } },
    { "id": "record_line", "type": "FUNCTION", "label": "Record item",
      "config": { "function_name": "record_line",
        "input_mapping": {
          "merchant": { "type": "expression", "value": "loop.line.merchant" },
          "amount": { "type": "expression", "value": "loop.line.amount" } } } },
    { "id": "cooldown", "type": "WAIT_UNTIL", "label": "Cooldown",
      "config": { "timeout_seconds": 60 } },
    { "id": "end", "type": "END", "label": "Done" }
  ],
  "edges": [
    { "id": "e1", "source": "intake", "target": "parse" },
    { "id": "e2", "source": "parse", "target": "approval" },
    { "id": "e3", "source": "approval", "target": "approved_route" },
    { "id": "e4", "source": "approved_route", "target": "end", "label": "not approved (default)" },
    { "id": "e5", "source": "line_loop", "target": "cooldown" },
    { "id": "e6", "source": "record_line", "target": "line_loop" },
    { "id": "e7", "source": "cooldown", "target": "end" }
  ]
}
```

## Running, Debugging, Resuming

```bash
lemma pods import ./my-pod/workflows/intake --dry-run && lemma pods import ./my-pod/workflows/intake

lemma workflows run intake --data '{"raw":"REQ-1001 …"}'
# creates the run and submits --data to the entry form; --wait polls through
# agent/function/timer waits and returns when a human form needs someone else

lemma workflows runs list intake             # recent runs
lemma workflows runs get <run-id>            # full run state incl. active_wait
lemma workflows runs submit-form <run-id> --data '{"approved": true}'   # submit the active form (--node to target explicitly)
lemma workflows runs waiting                  # your approval queue (form waits assigned to you)
lemma workflows runs cancel <run-id>          # kill a stuck/unwanted run
```

Workflow definition commands: `lemma workflows list | get | create | update | update-graph | delete`. Run commands live under `lemma workflows runs <verb>`; `run` (create + auto-submit to the entry form) stays top-level like `agents run` / `functions run`.

Run statuses: `RUNNING → WAITING ⇄ RUNNING → COMPLETED | FAILED | CANCELLED`. What a `WAITING` run is waiting on is `active_wait.wait_type` (`HUMAN`, `AGENT`, `FUNCTION`, `TIME`).

Debugging a run, in order:

1. `runs get` → check `status`, `current_node_id`, `failed_node_id`, `error`. Missing-path failures name the exact expression and input.
2. Read `step_history` — every executed node with status, output, error, timestamps. This shows exactly which mapping or condition misbehaved.
3. A run stuck in `WAITING`: read `active_wait` — `HUMAN` shows the form node and assignee, `AGENT` carries the conversation id in `external_ref` (inspect with `lemma conversations messages`), `FUNCTION` the function run id, `TIME` the wake time. Forms are completed via the app/frontend or `runs submit-form --data`.
4. Wrong branch taken: read the DECISION node's output in `step_history` (`matched_condition` shows which rule fired, `null` means the default edge) and re-check rule conditions against the context — remember literals need backticks (`` == `true` ``, `` > `0` ``).

## Limits & Gotchas

- Import **replaces the whole graph** — there's no node-level merge. The bundle is the source of truth.
- Edge `condition` labels are decorative. Routing = DECISION rules + the default edge (the **first-listed** outgoing edge); never let that default edge share a target with a rule, or unmatched inputs get mis-routed silently.
- Non-manual `start` types need a `start.config` (`SCHEDULED`/`DATASTORE_EVENT`/`EVENT`); DATASTORE_EVENT exposes the changed record's id at `start.metadata.record_id`, not `start.payload.*`.
- `input_mapping` strings are not auto-expressions; always `{"type": "expression"|"literal", "value": ...}`.
- Node ids `start` and `loop` are reserved.
- Agent nodes need agents with output schemas for reliable downstream mapping.
- **Delegated identity per run.** `FUNCTION`/`AGENT` nodes run as the **run owner** under the callee's own grants, RLS-scoped to that user — they can't read another member's rows. A workflow that must span all members' data needs **shared** tables (RLS off), and each callee still needs explicit grants or its node fails with `MISSING_WORKLOAD_RESOURCE_GRANT`.
- Verify each function/agent independently before wiring it into a graph — graph debugging is slower than unit testing.

## Verify

- Run with a realistic form payload; confirm `COMPLETED` and inspect `step_history` for every expected node.
- For each FORM: confirm the wait appears in the assignee's `lemma workflows runs waiting` queue, submit as that member, confirm other members get 403.
- For each DECISION: drive both branches with test payloads; don't infer routing from edge labels.
- For `FUNCTION`/`AGENT` nodes: confirm each callee is granted its tables/files/connectors (no `MISSING_WORKLOAD_RESOURCE_GRANT`), and that RLS reads match the run owner's seat.
- Confirm final table/file state matches the business outcome, not just the run status.

## See also

- The model → `pod-model.md` · the deterministic step → `functions.md` · the judgment
  step → `agents.md`
- Structured state the graph reads/writes → `tables.md` · documents → `files.md`
- Non-manual starts (cron / datastore / webhook triggers) →
  `schedules-and-triggers.md` · external app events → `connectors.md`
- A workflow inbox inside a browser app → `apps.md` (+ `app-recipes/workflow-form.md`)
- Start runs & clear form queues in an existing pod → the `lemma-user` skill
