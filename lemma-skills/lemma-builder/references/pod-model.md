# How a Pod Works — the model everything else builds on

This is the one mental model that the rest of the skill assumes. Every other doc
(tables, files, functions, agents, workflows, apps, connectors…) grounds its own
guidance in the pieces below, so read this first and refer back to it.

## A pod is one team's operating system

A **pod** is a shared workspace inside an organization that holds everything one
use case needs, under one permission boundary. It has three layers that share a
single identity model:

- **Data layer** — **tables** (structured rows) and **files** (documents + the
  built-in search index over them).
- **Automation layer** — **functions** (deterministic Python), **agents** (LLM
  workers), **workflows** (DAGs over functions + agents + *humans*), **schedules/
  triggers** (time, row-events, webhooks), and **connectors** (third-party apps).
- **Interface layer** — **apps** (browser UIs), **widgets** (inline live views),
  and **surfaces** (agents on Slack/Teams/email/…).

Members (humans) and **workloads** (agents, functions) act on the same resources.
Good pod scope = *one team, one operating loop, one coherent data model.*

## Identity & permissions — the spine

Two ledgers drive everything — **human roles** and **workload grants** — and a named
workload is governed by its grants alone (**grant-first**: an explicit workload grant
is standalone authority; the invoking user's role isn't also required).

1. **Zero access by default.** A freshly created agent or function can touch
   *nothing* — no tables, no folders, no connectors. Every resource is granted
   explicitly. Grants are **name-based and portable** (a table name, a folder
   path like `/knowledge`, a connector id) so they survive export/import into any
   pod. A missing grant surfaces at runtime as `MISSING_WORKLOAD_RESOURCE_GRANT`.
2. **Delegated identity.** When a function or agent runs, it acts **as the user
   who invoked it**, not as some service account. So row-level security and the
   personal `/me` file area resolve to *that* user. A workload never has its own
   `/me` and never sees more rows than the invoking user would — it just has the
   subset of capabilities you granted, exercised on that user's behalf.
3. **Destructive actions are gated.** No workload — not even the default pod agent
   that otherwise mirrors your permissions — can delete tables/agents/functions/
   workflows/apps/schedules, manage members/roles, or manage connector accounts by
   default. Unlock it with either an **explicit grant** of that permission on the
   workload (standing authority — needed for headless schedules/webhooks) or a live
   **`request_approval`** the user answers with "approve for session" (works for the
   rest of that conversation). Denied attempts surface as
   `DESTRUCTIVE_ACTION_REQUIRES_APPROVAL`.

**Pod member roles** (humans): `VIEWER` < `USER` < `EDITOR` < `ADMIN`. Roles gate
member-facing actions; workload grants are separate and additive.

**Common deny codes**: `MISSING_WORKLOAD_RESOURCE_GRANT` (grant the workload the
resource), `DESTRUCTIVE_ACTION_REQUIRES_APPROVAL` (grant it or approve),
`INSUFFICIENT_PERMISSION` (a human-role gap), `DELEGATION_SCOPE_VIOLATION`,
`PERSONAL_RESOURCE_DENIED` (another user's private resource). The full model, with
payloads and a decoder table, is in `authorization-model.md`.

## Data layer — tables

Tables are typed-column row stores. Columns: `TEXT, FILE_PATH, INTEGER, FLOAT,
BOOLEAN, JSON, DATE, DATETIME, UUID, USER, VECTOR, SERIAL, ENUM`.

**System columns are auto-managed — you never define or write them:**

- `id` — UUID v7 primary key, auto-generated, **time-sortable** (newest rows sort
  last by id).
- `created_at` / `updated_at` — always added; stamped by the backend on insert and
  update. Not user-writable; don't model your own.
- `user_id` — added **only** when the table has RLS on (the owner of the row).

**Row-Level Security (RLS) is the single most important table decision:**

- **RLS on** (the default) → each member sees and edits **only their own rows**
  (`user_id == them`); a new row is owned by whoever created it. Use for personal
  data: a member's tasks, drafts, preferences, per-user inbox.
- **RLS off** (`enable_rls: false`, "shared") → rows are **shared pod-wide**; every
  member sees the same data. Use for the team's shared state: tickets, leads,
  the catalog, anything the whole pod works on together.

RLS is enforced everywhere — record APIs **and** the read-only query API — and it
applies to **everyone, including pod admins**, by default. To deliberately read or
write across all users' rows you pass **`mode=ADMIN`** on the request, which
requires table-admin permission (otherwise `403`). Workloads are always
user-scoped; they cannot silently see another user's rows.

## Data layer — files & built-in search

One pod file tree:

- **`/`… (pod-shared)** — e.g. `/knowledge`, `/contracts`. Visible to members
  subject to grants. **Folder grants cascade**: granting `/knowledge` grants every
  file and subfolder under it.
- **`/me` (personal)** — each user's private area; resolves internally to that
  user's own subtree. A workload's `/me` is the *invoking user's* `/me`.

**Documents are auto-indexed for search on upload.** Indexable document types
(PDF, DOCX/DOC/ODT, Markdown, plain text, HTML, EPUB, RTF) are extracted, chunked,
and embedded automatically — **the pod *is* the RAG system**, no external vector
DB. Data files (CSV, JSON, XLSX, images, …) are **stored but not indexed** — keep
those as structured data or render them yourself.

Each indexed document gains **derived child artifacts** addressed under its path:

- `…/document.md` — full converted markdown, carrying `<!-- PAGE N -->` markers
  (1-based) so you can slice by page.
- `…/pages/page_0001.jpg` … — rendered page images (great for view-image).
- `…/images/image_0.png` … — extracted figures.

These are produced **automatically on upload** — the primary way to read a pod
document (markdown, page images, figures) with **no parsing step**. For a document
*outside* the pod (e.g. a PDF an agent fetched from the web), or as a fallback when an
artifact is missing, use the `liteparse-documents` skill instead.

**Search** is semantic+keyword (`HYBRID` default; `VECTOR` or `TEXT` available),
returns chunks **with page numbers**, and is **scoped by folder**: pass a
`scope_path` plus `SUBTREE` (the folder and everything beneath it — the powerful
default) or `DIRECT` (immediate children only). Sub-folder scoping is how you keep
an agent's retrieval tight (`search "refund policy" --scope /knowledge/billing`).

## How the resources interact

```
schedules ──start──▶ workflows ──orchestrate──▶ functions (deterministic)
   ▲ TIME · DATASTORE · WEBHOOK        │            agents (judgment → rich JSON)
   │                                   └── FORM ──▶ humans (approve / assigned)
   └─ event ── a row changes (DATASTORE) · a connector fires (WEBHOOK)
tables & files ◀── read/write ── functions · agents · workflows · apps
   └─ a workload writes a row ─▶ fires a DATASTORE schedule ─▶ another workload reacts
apps ◀── watchChanges (WebSocket, live UI) ── tables
connectors ◀── execute (delegated account) ── functions · agents
surfaces ◀── converse ──▶ humans   (its agent can also start workflows / functions)
```

- **Workflows** are the process layer: a DAG of `FORM` (human step), `AGENT`
  (judgment), `FUNCTION` (deterministic), `DECISION`, `LOOP`, `WAIT_UNTIL` nodes
  with a durable run context (JMESPath expressions reference prior node outputs and
  the trigger payload).
- **Schedules/triggers** start an agent or a workflow automatically. They're either
  **time-based** (`TIME` — cron / one-shot) or **event-based**, and event-based has two
  sources: a **table write/update** (`DATASTORE`) or a **connector event** (`WEBHOOK`).
  The table-event source is what makes **reactive choreography** possible — one workload
  writes a row, a `DATASTORE` schedule fires, another workload picks it up. This is the
  backbone of complex pods (mind the trigger-loop guardrail in `schedules-and-triggers.md`).
- **Connectors** are org-global capabilities (gmail, slack…): a **connector**
  (catalog entry) → an org **auth-config** → each user's **account** → executable
  **operations** / **triggers**. Workloads execute operations via the invoking
  user's connected account (delegated), never touching raw credentials.
- **Apps / widgets / surfaces** are the human interfaces over the same tables, files,
  agents, and workflows — pod-authenticated, so each sees exactly what RLS allows.
  **Surfaces** are *conversational*: a human chats with a pod agent on Slack / Teams /
  WhatsApp / email (and that agent can itself start functions, workflows, or other
  agents). **Apps** are browser UIs that can stay **live** by subscribing to table
  changes over WebSocket (`datastore.watchChanges`) — client-side reaction for fresh UI,
  distinct from a server-side `DATASTORE` schedule that *does work*.

## Choosing a primitive — design heuristics

When the model offers several ways to do something, these named rules pick one. The
rest of the skill refers back to them by number; `pod-design.md` turns them into
decision tables.

1. **One step, one agent.** An agent does everything one judgment pass can and returns
   **rich, multi-field JSON** (an `output_schema`). Add a *second* agent only for a
   genuinely **orthogonal** judgment — never for a sub-step of the same one. One rich
   agent beats an agent→agent chain: faster, cheaper, and testable in a single call.
2. **Workflow = checkpoints + humans.** Reach for a workflow when humans need to **see
   progress, approve, or be assigned steps**, or when deterministic and judgment steps
   interleave in a durable, observable, resumable sequence. A single continuous judgment
   with no human checkpoint is *just an agent* (possibly with tools) — not a workflow.
3. **Surface = a human is talking.** If a person converses on a chat platform, it's a
   **surface** — always. If a system event drives unattended work, it's an
   **event-based schedule** → workflow or agent. The dividing line is *who initiates*: a
   human message vs. an event. (A surface's agent can still start workflows mid-chat.)
4. **Events choreograph workloads.** A connector trigger or a table write/update starts
   the next workload through a schedule. Designing workloads that write rows others
   react to is how complex pods stay simple — each piece does one thing.
5. **Occam's razor.** Build the simplest graph — the fewest agents and nodes — that
   satisfies the use case. Collapse adjacent agent nodes into one rich-output agent
   unless they're orthogonal; drop a workflow that wraps a lone function call.
6. **Reach for the most direct primitive.** A single record write is a **direct
   records-API call**, not a function. A bare **agent** is called directly, granted as a
   tool, or used as a workflow node — never wrapped in a function. A **function earns its
   place** only when deterministic work spans multiple steps: several writes at once,
   writes + computation, or a third-party connector call (or a mix). Compose by
   **granting** — functions-as-tools (`function_<name>`), agents-as-tools
   (`agent_<name>`) — not by nesting one primitive inside another.

Corollaries that prevent common mistakes:

- **Agents are typed.** With `output_schema` an agent returns a structured dict the
  caller can route on; without it, a string (it may emit JSON, but **uncontracted** —
  don't depend on the shape). Add a schema whenever the output feeds a workflow node,
  another agent, or any router.
- **Server-side vs. client-side reaction.** To *do work* when a row changes, use a
  `DATASTORE` schedule. To keep a UI *fresh* when a row changes, use
  `datastore.watchChanges` (WebSocket) in the app — never poll a table.
- **Pod files are self-parsing.** A pod document is auto-converted to markdown + page
  images + figures (above), read via pod tools / `lemma file`. Use `liteparse-documents`
  (`lit`) only for documents **outside** the pod, or as a **fallback** when a derived
  artifact is missing.

## How a pod is authored

A pod is a **local folder bundle**, imported progressively (upsert by resource
name). Folder name **must equal** the resource's `name`. Import is
dependency-ordered: **tables → files → functions → agents → workflows →
schedules → connectors/surfaces → app → seed**. Two things do **not** round-trip
through bundles and must be set up by CLI + recorded in the README: **file
contents** (use a `seed/` script) and **connector** auth-configs/accounts.
Workload **grants** and surfaces *do* travel in bundles.

## See also

- Build a pod from a problem statement → `pod-design.md`
- Bundle format & import/export → `cli-and-bundles.md`
- Each resource in depth → `tables.md`, `files.md`, `functions.md`, `agents.md`,
  `workflows.md`, `connectors.md`, `schedules-and-triggers.md`, `surfaces.md`,
  `apps.md`
- Operate an existing pod (the runtime/CLI view of this same model) → the
  **lemma-user** skill.
