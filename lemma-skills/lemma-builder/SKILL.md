---
name: lemma-builder
description: "Design and build complete Lemma pods: model tables/files/functions/agents/workflows/schedules/connectors/surfaces/apps from a problem statement, author a local pod bundle, import progressively with the Lemma CLI, and verify every layer. Use for pod design, creation, restructuring, import/export, and app development. Do not use for day-to-day operation of an existing pod; use lemma-user instead."
---

# Lemma Builder

## What Is A Pod

A **pod** is one team's operating system: a shared workspace inside an organization
that holds everything one use case needs — **tables** and **files** (data, with
documents auto-indexed for built-in RAG), **functions**, **agents**, **workflows**,
**schedules**, and **connectors** (automation), and **apps** and **surfaces**
(interfaces) — under one permission boundary. Two rules run through all of it: every
workload starts with **zero access** and is granted resources explicitly by name,
and a workload acts under the **delegated identity** of the user who invoked it (so
RLS and the personal `/me` area resolve to that user). Good pod scope = one team,
one operating loop, one coherent data model.

> **Read `references/pod-model.md` first.** It is the canonical model every other
> doc grounds in — identity & permissions, the data/automation/interface layers,
> how the resources interact, and how a pod is authored. This file is the build
> entry point on top of it.

## The Resources

| Resource | What it is | What it solves |
| --- | --- | --- |
| **Tables** | Typed-column data store; per-table row-level security (RLS-on by default = each member's own rows; `enable_rls: false` = shared team data), foreign keys, enums | The pod's database: tickets, leads, approvals — durable structured state |
| **Files** | Document store under shared `/…` folders (e.g. `/knowledge`) and personal `/me`; uploaded documents are auto-indexed and semantically searchable — **built-in RAG** — with converted-markdown reading | The pod's knowledge and artifacts: contracts, manuals, reports, generated deliverables |
| **Functions** | Typed Python entrypoints run server-side as workload principals | Deterministic logic: validation, multi-table writes, external API calls, transforms |
| **Agents** | LLM workers with instructions, toolsets, and granted resources | Judgment: classification, drafting, extraction, research, conversation |
| **Workflows** | Node graphs of FORM / AGENT / FUNCTION / DECISION / LOOP / WAIT_UNTIL steps with durable runs | The process layer — orchestrates functions, agents, **and humans** (form nodes are assigned to pod members) |
| **Schedules** | Time-based (TIME cron) or event-based — DATASTORE (table row events) and WEBHOOK (connector events) | Starting agents or workflows automatically |
| **Connectors** | Third-party apps (Gmail, Slack, …) via org auth configs, accounts, and executable operations | Acting on external systems |
| **Surfaces** | A pod agent exposed on Slack/Teams/Telegram/WhatsApp/Gmail/Outlook | Meeting users where they already chat |
| **Apps** | Custom browser apps deployed into the pod — single-file HTML (no build) for one page, or Vite + lemma-sdk for multi-page apps | The product UI: dashboards, queues, detail views, workflow inboxes |

**Choosing among them — six heuristics** (full text in `references/pod-model.md` → "Choosing a primitive"; `pod-design.md` turns them into decision tables):

1. **One step, one agent.** One agent returning rich `output_schema` beats an agent→agent chain; split only for *orthogonal* judgments.
2. **Workflow = checkpoints + humans.** Reach for a workflow when people approve, are assigned steps, or watch progress — not for a lone call.
3. **Surface = a human is talking.** A chat-platform conversation is a surface; a *system event* driving unattended work is a schedule → workflow/agent.
4. **Events choreograph workloads.** A table write or connector trigger starts the next workload (server-side); an app stays live via `watchChanges` (client-side).
5. **Occam's razor.** Build the fewest agents and nodes that satisfy the use case.
6. **Reach for the most direct primitive.** A single record write is a direct records-API call, not a function; a bare agent is called directly or granted as a tool — never wrapped. A function earns its place only for deterministic multi-step work (coordinated writes, write + compute, or a connector call).

## The Build Loop

Pods are built as local directory bundles and imported with upsert. This is the primary path — inline `lemma <resource> create --data ...` is only for quick experiments.

**Scaffold, don't hand-write.** `lemma <resource> init` writes a near-runnable, commented bundle file (JSONC — `//` and `/* */` comments and trailing commas are allowed) with the right shape, folder==name wiring, and the backend defaults (visibility, RLS, function headers). Edit it, then import.

```bash
lemma pod init my-pod                  # whole starter pod on disk (pod.json + table + agent + AGENTS.md)
# or scaffold into an existing bundle (auto-finds pod.json upward):
lemma tables init tickets [--shared]   # tables/tickets/tickets.json  (--shared = enable_rls:false)
lemma functions init score_ticket      # functions/score_ticket/{score_ticket.json,code.py} with headers
lemma agents init triage [--runtime ID] # agents/triage/{triage.json,instruction.md}
lemma workflows init intake            # a valid FORM->END graph to extend
lemma schedules init nightly           # schedules/nightly/nightly.json
lemma surfaces init slack              # surfaces/slack/slack.json

lemma agents grant triage tickets:read,write /knowledge:read app:gmail:use agent:helper:execute function:score_ticket:execute
#   ^ merges into permissions.grants in place (keeps your JSONC comments): name:perms (table) | /path:perms (folder) | app:name:use | agent:name:execute | function:name:execute
lemma agents schema                    # or `lemma schema agent` — print the example/shape for a resource type

lemma pods create my-pod --with-starter   # create the pod AND scaffold+import a starter in one shot
```

By hand, or after scaffolding — the bundle is always the source of truth:

```bash
lemma pods create my-pod --org <org> --description "..."   # import never creates the pod shell

mkdir my-pod && cd my-pod                                  # author the bundle locally
# pod.json + one folder per resource (see layout below)

lemma pods import ./my-pod --dry-run                       # validate, fix errors
lemma pods import ./my-pod                                 # upsert by resource name (default)
lemma pods import ./my-pod/functions/score_ticket          # partial imports work too
lemma pods doctor my-pod                                   # check grants/targets/surfaces wiring

lemma functions run score_ticket --data '{"title":"test"}' # test each layer
lemma workflows run intake --data '{"id":"REQ-1"}'         # waits for completion by default
lemma agents chat triage-agent "Classify this ticket"

# edit files -> re-import -> re-test. Export an existing pod for a baseline (or a template):
lemma pods export ./bundles --force [--as-template]
```

Bundle layout (folder name must equal the resource's `name`):

```text
my-pod/
  pod.json
  tables/tickets/tickets.json
  functions/score_ticket/score_ticket.json    + code.py        (JSON carries permissions.grants)
  agents/triage-agent/triage-agent.json       + instruction.md (JSON carries permissions.grants)
  workflows/intake/intake.json
  schedules/nightly/nightly.json
  surfaces/slack/slack.json
  apps/ops-app/ops-app.json                + source/
  files/knowledge/.folder.json
  seed/seed.sh                                 # sample data so the pod demos itself (not imported — run after)
  payloads/                                    # test fixtures, not a pod resource
  README.md                                    # operator setup + verification runbook
```

Build order follows dependencies: **tables → files → functions → agents → workflows → schedules → connectors/surfaces → app → seed**. Verify each layer with realistic data before adding the next.

**Build for the hero moment, not just for correctness.** A pod that imports cleanly and passes its tests can still be plumbing nobody wants to open. Before building, name the one screenshottable "oh" — the agent doing real work on its own, behind an interface someone adopts (see `references/pod-design.md`). The **app (or surface) is usually the product** — design it like the thing people live in, not an afterthought tacked on last. And **seed the pod so it demos itself**: a `seed/seed.sh` of sample rows, files, and one completed run, so opening the app shows the hero moment immediately instead of an empty state. (Records and file contents don't round-trip through import — the seed script is how they land; record it in the README.)

Three rules that bite everyone:

1. **Zero access by default, and no destructive power without a grant or approval.** Agents and functions are created with NO access to anything — not tables, not files/folders, not connectors. Every resource they touch must be granted explicitly, either via `permissions.grants` in their bundle JSON (exported automatically, replaced on import) or `lemma functions|agents permissions replace <name> --file grants.json`. A named workload's grant is standalone authority (grant-first). `MISSING_WORKLOAD_RESOURCE_GRANT` at runtime means a grant is missing; `DESTRUCTIVE_ACTION_REQUIRES_APPROVAL` means a delete/manage action needs either an explicit destructive grant or a user's session approval. Full 403 decoder + the complete model in `references/authorization-model.md`.
2. **Not everything bundles.** File contents and connectors (auth configs, accounts) are not part of import/export — set those up with CLI commands and record the steps in the pod's README. Surfaces and workload permissions do round-trip in bundles.
3. **Leave a runbook.** Every production-quality bundle should include a README with: purpose, required CLI context, non-bundled setup steps, required uploaded files, connector auth configs/accounts, verification payloads, and the final end-to-end smoke test.

## References

Read what the task needs:

- `references/pod-model.md` — **the canonical model. Read this first.** Identity & permissions, the data/automation/interface layers, how the resources interact, how a pod is authored.
- `references/authorization-model.md` — the two ledgers, grant-first rule, delegated identity, destructive-action gate + session approvals, the 403-code decoder, agent/function-as-tool grants, connector account modes, import/export grant semantics. Read when a workload hits a 403 or you're wiring grants.
- `references/pod-design.md` — problem statement → pod architecture; decision tables; worked example; testing strategy. **Start here for new pods.**
- `references/cli-and-bundles.md` — auth/context, exact bundle format, a minimal quickstart bundle, import/export semantics and limits, command cheatsheet.
- `references/tables.md` — column types, schema JSON, records, RLS, design guidance.
- `references/files.md` — shared `/…` vs personal `/me`, search, converted markdown, file+table patterns.
- `references/functions.md` — code contract, in-function SDK (`Pod.from_env()`), grants, testing.
- `references/agents.md` — agent JSON, toolsets, instructions, agents/functions as tools (sub-agents), runtime profiles, testing.
- `references/workflows.md` — every node type, expressions, human-in-the-loop patterns, run debugging.
- `references/connectors.md` — connectors → auth configs → accounts → operations/triggers; LEMMA vs COMPOSIO providers; delegated execution.
- `references/schedules-and-triggers.md` — TIME/DATASTORE/WEBHOOK triggers, event payloads, LLM event filtering.
- `references/surfaces.md` — exposing a pod agent on Slack/Teams/Telegram/WhatsApp/Gmail/Outlook.
- `references/apps.md` — design-doc-first method, scaffold/dev/deploy, TS SDK, components, UX rules.
- `references/app-recipes/*.md` — copy-paste app patterns: agent chat, RLS tables, workflow forms, file viewer, connector actions (load the one you need).
