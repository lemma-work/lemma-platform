---
name: lemma-evals
description: Design, run, and review repeatable evaluations for Lemma agents, functions, and workflows. Use when defining evaluation contracts and case datasets, comparing a baseline with a candidate revision or runtime, checking deterministic correctness or rubric-scored judgment, measuring repeated-run variance, testing delegated identity, RLS, grants and side-effect safety, driving human checkpoints, triaging regressions, or preserving durable run evidence. Do not use for browser or visual app QA, or for testing whether a skill triggers.
---

# Lemma Evals

Evaluate the behavior of a real Lemma workload under the identity, runtime, grants,
data state, and approval policy it will have in use. Preserve the raw run identifiers
and state behind every score; never reduce an evaluation to a prose impression.

Use `lemma-app-qa` for browser journeys, layout, accessibility, and frontend defects.
Use `lemma-skill-creator` for skill trigger and instruction-following tests. Use this
skill for deployed agent, function, and workflow behavior, including any calls those
workloads make to one another.

## Define the contract first

Write the evaluation contract before running a candidate. Read
[references/evaluation-contract.md](references/evaluation-contract.md) when creating
or reviewing the suite; use its artifact schemas as conventions, not as a Lemma API.

Specify:

- the decision the evaluation gates and the target kind, name, pod, and environment;
- immutable baseline and candidate identities, or two versioned names/pods when the
  resource cannot be pinned in place;
- case inputs, setup state, acting member, expected observable outcome, and cleanup;
- required deterministic checks, rubric dimensions, thresholds, and critical
  invariants that no average may hide;
- repeat count and aggregation for non-deterministic cases;
- permitted side effects, approval points, stop conditions, and evidence retention.

Do not tune thresholds after seeing candidate outputs. Keep a holdout set when the
same cases are used repeatedly for prompt or graph iteration. Include ordinary,
boundary, malformed, denied-access, and recovery cases; a happy-path demo is not an
evaluation suite.

## Snapshot the targets

Capture definitions and permissions before execution. Prefer compact output while
orienting; save JSON for the durable snapshot.

```bash
lemma --output json agents get <agent> --pod <pod>
lemma --output json agents permissions get <agent> --pod <pod>

lemma --output json functions get <function> --pod <pod>
lemma --output json functions permissions get <function> --pod <pod>

lemma --output json workflows get <workflow> --pod <pod>
```

Record the agent instruction, toolsets, output schema, runtime profile, and grants;
the function type, input/output schemas, code or revision identity, and grants; or the
workflow start, nodes, edges, mappings, and callees. Snapshot every called agent or
function too. A workflow node runs as the workflow run owner but under the callee's
own grants, so the graph alone does not describe its effective behavior.

Never overwrite the only baseline to create a candidate. Use an isolated eval pod,
two versioned resources, or a reproducible bundle revision. Function schemas are not
updated by ordinary upsert, so treat a schema change as a versioned target or recreate
it deliberately outside the evaluation run.

## Build replayable cases

Give every case a stable id. Keep the input separate from the expected result and
scorer. Declare fixtures as data, not as undocumented shell history. Use unique case
ids in created rows/files so state can be attributed and cleaned without broad
deletes.

Keep team-wide cases in a shared pod folder such as `/evals/<suite>/cases.jsonl`.
Keep sensitive personal fixtures under the relevant member's `/me`; do not copy
tokens, connector credentials, or another member's private data into the suite.
Use a shared results table only when the pod already models evaluations or the user
has authorized that pod change.

For identity-sensitive suites, declare an actor matrix. Run each case from a separately
authenticated member context and confirm the returned `user_id` where the run schema
exposes it. Never simulate another member by editing expected owner ids or by placing
their credentials in artifacts.

## Choose the narrowest valid scorer

Run deterministic checks before any model-based rubric:

- require the expected terminal status and absence or presence of a specific error;
- validate output schema, required fields, types, enums, numeric tolerances, and
  invariant predicates;
- inspect the durable table/file/connector outcome, not only the returned text;
- assert the exact authorization denial code when denial is the expected behavior;
- verify the expected workflow nodes, branch, wait, and approval transition.

Use a rubric only for judgment that cannot be expressed as a predicate. Define
non-overlapping dimensions, anchored score levels, weights, must-pass dimensions, and
evidence requirements. Blind the grader to baseline/candidate labels and case authors'
preferred answer. Pin and record the grader instruction and runtime. Treat grader
failure or malformed output as evaluation infrastructure failure, not target failure.

Do not exact-match free-form agent prose unless wording itself is the contract. Do
not let a high style score compensate for an unauthorized read, unsafe action,
fabricated citation, missing required field, or failed human gate.

## Protect people and state

Run read-only and deterministic cases first. For side-effecting cases:

- use an isolated pod, sandbox connector, draft-only path, or uniquely namespaced
  fixture whenever possible;
- take a before-state snapshot and declare the exact allowed delta;
- reset state between baseline, candidate, and repetitions, or prove the operation is
  idempotent;
- require a human checkpoint before external sends, payments, destructive actions,
  member changes, or production connector writes;
- stop on the first unexpected privileged or irreversible action.

Do not blanket-approve an agent while evaluating its approval behavior. Inspect
`lemma conversations approvals <conversation-id>` and approve or deny the specific
request with
`lemma conversations approve <approval-id> --conversation <conversation-id>` (add
`--deny` to reject). Always pass the approval id: omitting it resolves every pending
request. Use session approval only when the contract explicitly requires it. An
attempted unsafe action is evidence even if the platform blocks it.

## Execute and collect native evidence

Use JSON output for captured run objects. Pass larger inputs with `--file` to avoid
quoting drift.

### Agents

```bash
lemma --output json agents run <agent> "<message>" --no-wait --pod <pod>
lemma --output json conversations get <conversation-id> --pod <pod>
lemma --output json conversations messages <conversation-id> --pod <pod>
lemma --output json conversations approvals <conversation-id> --pod <pod>
lemma conversations stream <conversation-id> --pod <pod>
```

Each agent run is a conversation. Preserve the returned `conversation_id` and
`agent_run_id`, the transcript, structured output, tool/approval evidence,
`last_run_status`, `last_run_error`, runtime, timestamps, and acting `user_id`. Start
a fresh conversation per independent repetition; reuse one only when conversation
memory is part of the contract.

### Functions

```bash
lemma --output json functions run <function> --file <input.json> --pod <pod>
lemma --output json functions runs get <function> <run-id> --pod <pod>
```

The default waits for an async job; use `--no-wait` only when the harness will poll
the run. Preserve `status`, `input_data`, `output_data`, `logs`, `error`,
`revision_hash`, `user_id`, and the created/started/completed timestamps. A
`COMPLETED` status does not prove the expected write occurred; query the affected
record or file separately.

### Workflows

```bash
lemma --output json workflows run <workflow> --file <form-input.json> --no-wait --pod <pod>
lemma --output json workflows runs get <run-id> --pod <pod>
lemma --output json workflows runs waiting --pod <pod>
lemma --output json workflows runs submit-form <run-id> --file <decision.json> --pod <pod>
lemma workflows runs cancel <run-id> --pod <pod>
```

Preserve `status`, `user_id`, `current_node_id`, `failed_node_id`, `error`,
`execution_context`, `step_history`, `active_wait`, and timestamps. `WAITING` is the
human-form state. Agent, function, and timer suspensions remain `RUNNING` and identify
their platform work through `active_wait.wait_type` (`AGENT`, `FUNCTION`, or `TIME`).
For a human gate, require `active_wait.wait_type == HUMAN`, verify that the intended
assignee sees it in `runs waiting`, submit the documented decision as that member,
and verify the next state. Do not interpret a still-running platform wait as failure.

## Test delegated authority explicitly

Exercise authorization as a first-class contract, not only as failure debugging:

- confirm RLS reads and writes resolve to the invoking member and do not expose
  another member's rows;
- confirm `/me` resolves to that member's private tree;
- confirm named agents and functions can access only explicitly granted resources;
- expect `MISSING_WORKLOAD_RESOURCE_GRANT` for a missing workload grant and
  distinguish it from a member-role `INSUFFICIENT_PERMISSION` failure;
- confirm connector calls use the intended user-owned or explicitly pinned account;
- confirm called functions and agents use their own grants rather than inheriting
  the parent workload's grants.

Treat the built-in default pod assistant as the explicit exception: it has no named
Agent entity or workload grants and mirrors the invoking member's pod permissions,
while destructive actions still require approval. Record the member role and pod
runtime instead of inventing a permission snapshot for it.

Run positive and negative cases. A suite that proves permitted access but never
attempts forbidden access does not establish the boundary.

## Repeat and compare fairly

Run deterministic functions once per fixture unless checking concurrency or flakiness.
Run judgment-heavy agent cases at least three times by default; increase repetitions
for rare safety failures or scores near the gate. Use identical actors, fixtures,
runtime settings, grader, and case order policy for baseline and candidate. Randomize
or alternate execution order when shared environmental drift could bias one side.

Report per-case pass rate, aggregate score, worst repetition, and variance. Report
critical failures as counts and raw case ids; never average them away or cherry-pick
the best completion. Mark timeouts, unavailable runtimes, grader errors, and fixture
failures as infrastructure outcomes so they do not silently become zeros or passes.

Measure latency from native timestamps where available and also capture client
wall-clock time. Record tokens and cost only when an emitted run event or authorized
usage view attributes them unambiguously to the run; otherwise store `null`. Do not
reconstruct billed cost from a guessed model price.

## Triage regressions from evidence

Replay one failing candidate case against a fresh fixture before generalizing. Compare
the raw baseline and candidate evidence, then classify the first divergence as one of:

- contract or case defect;
- fixture/data drift;
- acting identity, RLS, grant, or connector-account difference;
- target definition, runtime, model, or tool availability change;
- function error or revision mismatch;
- workflow mapping, branch, callee, wait, or form transition failure;
- scorer, grader, timeout, or collection failure.

For functions, start with `error`, `logs`, and `revision_hash`. For workflows, start
with `failed_node_id`, `error`, `step_history`, and `active_wait`; follow an AGENT wait
to its conversation and a FUNCTION wait to its function run. For agents, start with
`last_run_status`, `last_run_error`, transcript, tool calls, approvals, and runtime.
Fix or exclude broken evaluation infrastructure before judging the candidate.

## Preserve and report

Create an append-only result folder such as
`/evals/<suite>/<timestamp>-<evaluation-id>/` containing:

- the contract and exact case dataset or content hash;
- baseline, candidate, callee, permission, runtime, and grader snapshots;
- one machine-readable result record per case and repetition;
- raw run/conversation ids plus redacted evidence needed to reproduce each score;
- a short report with the gate decision, deltas, critical failures, variance,
  infrastructure errors, and recommended action.

Upload local artifacts with `lemma files upload`; do not leave the only copy in a
temporary workspace. Redact secrets and unnecessary personal content, but retain
stable ids and hashes. State whether the candidate passes the predeclared gate,
passes with accepted risk, or fails. Never claim statistical confidence that the
sample size does not support.
