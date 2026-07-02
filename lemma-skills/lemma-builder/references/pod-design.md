# Pod Design: From Problem Statement To Architecture

Designing a pod is one decision repeated at every layer: **what is the unit of
work, and who or what moves it through its lifecycle?** Answer that and the tables,
files, functions, agents, workflows, schedules, connectors, and interface fall out
in order. This doc is the plan-first method that turns a problem statement into a
named resource list — which becomes your bundle layout, your grants, and your
verification script. (Grounds in `pod-model.md` — read it first for the resource
model this method assembles.)

The output is a short design note (half a page) that maps directly onto the bundle
folders and the README. **Write it before authoring any bundle file.** Skipping it
is the most expensive mistake in the skill: resource names are permanent upsert
keys, and apps/workflows/grants reference resources by name, so renaming after the
fact is painful.

## The Method

Given a problem statement, answer these in order:

1. **Entities → tables.** What is the unit of work (ticket, lead, claim, applicant, invoice)? What lifecycle does it move through? Every lifecycle gets an `ENUM` status column; every relation gets a foreign key; every attached document gets a `FILE_PATH` column. Decide per table whether rows are **shared by the team** (`enable_rls: false`) or **private to each member** (`enable_rls: true` — the default; RLS scopes each member to their own rows). See `tables.md`.
2. **Knowledge → files.** What documents exist (policies, contracts, manuals)? Files are the pod's **built-in RAG layer** — uploaded documents are auto-indexed and semantically searchable, scoped by folder, with no vector DB to run. Plan the shared `/…` folder layout (e.g. `/knowledge`, `/contracts`) so retrieval can be scoped by path. Prose/documents people read or search go in files; structured data you filter/sort/join goes in tables (CSV/JSON/XLSX uploaded as files are stored but never indexed). What artifacts will the pod produce, and do they go to `/me` (per-user, private) or a shared folder?
3. **Steps → functions, agents, humans.** Walk the process step by step and classify each one:
   - deterministic (validate, compute, coordinated writes, call an API) → **function**
   - judgment (classify, draft, extract, decide-with-reasoning) → **agent**
   - human decision or data entry → **workflow FORM node** assigned to a pod member

   **One agent per cohesive judgment** (pod-model heuristic #1): when several judgment sub-steps are really *one* reasoning pass — classify *and* extract *and* draft from the same input — do them in **one agent** that returns rich multi-field JSON (`output_schema`). Don't split a single judgment across agent nodes. Add a second agent only for a genuinely **orthogonal** judgment; a continuous agent→agent chain is almost always a mistake.

   **Reach for the most direct primitive** (heuristic #6): a lone record write is a direct records-API call, not a function; a function earns its place only when the deterministic step does several writes at once, write + compute, or a connector call. And don't wrap an agent in a function — call it directly, or grant it as a tool.
4. **Process → workflow.** Reach for a workflow when humans must **see progress, approve, or be assigned steps**, or when deterministic and judgment steps interleave in a durable, resumable, observable sequence (heuristics #2, #5). FORM nodes carry assignees, so any multi-actor process — code + agents + one or more humans — is naturally a workflow. A single continuous judgment with no human checkpoint is *just an agent*; single-step automation stays a bare function or agent. Keep the graph minimal — the fewest nodes that satisfy the use case.
5. **Starts → schedules.** When does work begin? A schedule is **time-based** (`TIME` — cron / one-shot) or **event-based**, and event-based has two sources: a **table write/update** (`DATASTORE`) or a **connector event** (`WEBHOOK`, e.g. inbound email). The table-event source enables **reactive choreography** — a workload writes a row, a `DATASTORE` schedule fires, another workload reacts; chain these for complex pods (heuristic #4), minding the trigger-loop/burst guardrails in `schedules-and-triggers.md`. (Manual runs need no schedule.)
6. **External actions → connectors.** Which third-party apps are touched (Gmail, Slack, Calendar)? Note the connector ids and the operations needed; they get wired through an org auth config and granted (`connector.use`) to the workloads that call them. Connectors are **not** part of the bundle — record their setup in the README (see `connectors.md`).
7. **Experience → chat, surface, or app.** How do users meet the pod? The interface is often the whole product — design it like the thing people will live in, not an afterthought. For an app, the design-doc-first method lives in `apps.md` → "Plan first".
   - occasional Q&A or ad-hoc tasks → agent chat is enough
   - users live in Slack/Teams/WhatsApp/email → **surface** (`surfaces.md`)
   - operators work queues daily, submit workflow forms, need dashboards → **app** (single-file HTML for one page/dashboard; Vite for a multi-page app)

   **Surface vs. event-workflow** (heuristic #3): if a *human converses* on a chat platform, it's a **surface**; if a *system event* (inbound mail treated as data, a row change) drives *unattended* work, it's an **event-based schedule → workflow/agent**. The same Gmail account can back both — a surface answers people conversationally while a `WEBHOOK` schedule runs the intake pipeline. See the *Reacting to events* table below.
8. **Hero moment → the one demo-able "oh".** Name the single thing this pod does that a one-shot chatbot or a CRUD form can't — the agent doing real work on its own, behind an interface someone adopts. Make it screenshottable in ~60 seconds with no narration ("the agent texts you on WhatsApp before declining the meeting"; "a stale lead turns red and the scout has already drafted the nudge"). The hero moment is the agentic unlock made visible — design the app, surface, and seed data so it lands the moment someone opens the pod. If you can't point to one, the pod is plumbing, not a product; rethink before building.
9. **Seed it so it demos itself.** Sample rows, a few files, one completed workflow run — enough that opening the pod or app shows the hero moment immediately instead of an empty state. Put seed commands in a `seed/seed.sh` (CLI calls / a small SDK script) so anyone can reproduce the live demo after import. (File contents and records are not part of bundle import — the seed script is how they get there; record it in the README.)
10. **Success criteria.** Write the one end-to-end scenario that proves the pod works. This becomes your final verification script.

Keep the note short (half a page). Name every resource in it — those names become folder names in the bundle and are the upsert keys forever, so choose stable snake_case/kebab-case names you can live with. Copy the final resource list, non-bundled setup, and smoke test into `README.md` so the next builder can import, connect, seed, and verify the pod without rediscovering the plan.

## Decision Tables

### Function vs agent vs workflow

| Situation | Use |
| --- | --- |
| Deterministic work spanning multiple steps — coordinated writes, write + compute, or a connector call | Function (predictable, testable, auditable) |
| Needs reading comprehension, judgment, or generation | Agent |
| Output feeds another system | Function, or agent **with `output_schema`** |
| Several judgment sub-steps that are **one continuous reasoning pass** | **One agent** returning rich `output_schema` — not multiple agent nodes (heuristic #1) |
| Humans must **approve, be assigned, or see progress**; or code + agents + humans interleave durably | Workflow composing the above (heuristic #2) |
| One workload must call/compose an agent or a function (incl. "let the agent decide at runtime") | Grant it as a tool — one grant each: `agent.execute` → `agent_<name>`, or `function.execute` → `function_<name>` (the callee runs under its own grants; no mirroring) — or use a workflow node. **Never wrap one primitive in another** (heuristic #6; agents.md → "Agents & Functions as Tools") |
| One-off insert/update of a record | Neither — direct record ops (`lemma records create ...`), not a function (heuristic #6) |

### Table column vs JSON column vs separate table

| Situation | Use |
| --- | --- |
| Field is filtered, sorted, or displayed in queues | Typed column (`ENUM`, `TEXT`, `DATETIME`, …) |
| Flexible payloads, model outputs, app metadata, no SQL filtering yet | `JSON` column |
| Child rows with their own lifecycle, permissions, or audit needs (line items, comments) | Separate table with a `foreign_key` |
| Large document content | Never in the table — file + `FILE_PATH` column |

### Where data lives, and who sees it

| Situation | Use |
| --- | --- |
| Prose/documents people read or search (policies, manuals, contracts) | **Files** — auto-indexed, semantically searchable (built-in RAG) |
| Structured data you filter, sort, join, or show in queues | **Tables** (CSV/JSON/XLSX uploaded as files are stored but never indexed) |
| Each member should see only their own rows (personal tasks/settings/notes) | **RLS table** (`enable_rls: true`, the default) |
| All members share and edit the same rows (team queue, catalog, reference data) | **Non-RLS table** (`enable_rls: false`) |

### Entry point

| Users | Entry point |
| --- | --- |
| Ask questions, request ad-hoc work | Agent chat (`lemma chat`, frontend chat) |
| Already live in a chat/email product | Surface |
| Operate a queue daily, act on records, submit forms | App |
| Mix | App for operators + surface for requesters is a common pair |

### Reacting to events

| Trigger | Use |
| --- | --- |
| A human converses on a chat platform (Slack/Teams/WhatsApp/email) | **Surface** — the agent answers conversationally (`surfaces.md`) |
| A system event drives unattended work — a connector trigger or a table write/update | **Schedule** (`WEBHOOK` / `DATASTORE`) → **workflow or agent** (`schedules-and-triggers.md`) |
| One workload should react to what another wrote | `DATASTORE` schedule on the written table → the reacting workload (heuristic #4) |
| An app must reflect row changes live | `datastore.watchChanges` (WebSocket, client-side) — never poll (`apps.md`) |

### Parsing a document

- In, or going into, the pod → **pod auto-processing**: upload it and read the
  auto-produced `…/document.md`, `…/pages/*.jpg`, `…/images/*.png` via `lemma file`
  (no parsing step; it's also indexed for search).
- From the web/external, a local one-off, or a pod file **missing** its derived
  artifact (scanned/OCR, bounding boxes) → **liteparse** (`lit`, the
  `liteparse-documents` skill).

### Schedule target

A schedule is **time-based** (`TIME`) or **event-based** — a **table change**
(`DATASTORE`) or a **connector event** (`WEBHOOK`). Whichever fires it, the target is
exactly one agent or one workflow:

- Schedule → **agent** when each firing needs judgment over current state ("review stale tickets and nudge owners").
- Schedule → **workflow** when each firing should execute the same multi-step process ("nightly: load batch, loop, write report").

## Roles & Access

Pod **members** (humans) hold one of four roles, assigned by an admin. This is separate from the zero-default **workload grants** that functions and agents receive per resource (see `functions.md`).

| Role | Can |
| --- | --- |
| `POD_VIEWER` | Read tables, records, and files |
| `POD_USER` | + write records; run agents, functions, and workflows |
| `POD_EDITOR` | + create/update tables; write files |
| `POD_ADMIN` | + delete tables/files; manage members and roles |

Record writes require `POD_USER` or above on **both** RLS and shared tables — RLS changes *which rows* a member touches, not *whether* they can write. On an RLS table, `POD_ADMIN` is the only role that sees and manages every member's rows; everyone else is scoped to their own.

## Worked Example: Support Triage Pod

Problem statement: *"Customers email support requests. We want them triaged automatically, high-risk ones approved by a human, everything tracked, and a dashboard for the support team."*

Applying the method:

1. **Entities:** `tickets` (title, body, status ENUM new/triaged/waiting_approval/resolved, priority ENUM, category, owner USER, source_email JSON, attachment FILE_PATH) and `ticket_events` (ticket_id FK, kind, note) for audit.
2. **Knowledge:** `/knowledge` for the support playbook and refund policy PDFs — agents search these to ground triage decisions.
3. **Steps:** parse + persist email → function `create_ticket`; classify severity/category with policy context → agent `triage-agent` (output_schema!); approve high-priority handling → FORM assigned to support lead; write final state + notify → function `resolve_ticket`.
4. **Workflow:** `ticket-intake`: FUNCTION create_ticket → AGENT triage → DECISION (priority == 'high' → approval FORM, else skip) → FUNCTION resolve_ticket → END.
5. **Starts:** WEBHOOK schedule on the Gmail trigger for new inbound mail, plus manual runs for testing.
6. **Connectors:** `gmail` (read inbound, send replies).
7. **Experience:** app `support-app` — queue of tickets, detail pane, pending approval forms inbox, agent chat panel.
8. **Hero moment:** a support email lands and seconds later the app shows a fully triaged ticket — severity, category, suggested reply — with the one high-risk case already waiting in the lead's approval inbox. Nobody clicked through a form to make that happen.
9. **Seed:** `seed/seed.sh` loads a handful of tickets across statuses (plus one parked on an approval) so the app shows a live queue, not an empty table, the instant it opens.
10. **Success:** send a test email → ticket exists, triaged, approval form appears for the lead, submitting it resolves the ticket and sends a reply, app shows the whole journey.

Use this worked example as a design pattern, not as a referenced bundle. If you implement it, create the bundle from the named resources above and document any Gmail auth config, uploaded policy files, seed payloads, and verification steps in the pod README.

## Anti-Patterns

- **The everything-pod.** Support + finance + hiring in one pod because they share a team channel. Split unless they truly share one operating loop and data model.
- **Agent-as-database.** Asking an agent to "remember" state across conversations. Durable state lives in tables; the agent reads/writes them.
- **Function-as-agent.** A function that calls an LLM to make a judgment call. Use an agent node; you get conversation history, instructions, and toolsets for free.
- **Workflow for a single function call.** Adds latency and ceremony; call the function directly.
- **Wrapper function.** A function that just calls an agent, or that does a single record write. It adds ceremony and hides the work from grants and run history; call the agent directly (or grant it as a tool) and do single writes via the records API. Reserve functions for deterministic multi-step work (heuristic #6).
- **Over-decomposed agent chain.** Splitting one cohesive judgment into agent→agent→agent. One agent returning rich `output_schema` is faster, cheaper, and testable in a single call; add a second agent only for an *orthogonal* judgment (heuristic #1).
- **Wrong reactive primitive.** An event-triggered *workflow* built to "chat" with a human (that's a surface), a *surface* wired to run an unattended pipeline (that's a `WEBHOOK`/`DATASTORE` schedule), or an app that **polls** a table instead of `datastore.watchChanges`.
- **Skipping the design note.** Renaming resources after users depend on them is painful (names are upsert keys); apps and workflows reference resources by name.

## Success Criteria & Testing Strategy

Step 10 (success criteria) is not boilerplate — it is the **one end-to-end scenario
that proves the pod works**, and it becomes the script you run last. Write it as a
concrete walkthrough with real inputs and the observable result at each hop, e.g.
*"send a test email → a `tickets` row exists and is triaged → the high-risk case
shows an approval FORM in the lead's inbox → submitting it resolves the ticket and
sends a reply → the app shows the whole journey."*

Test **bottom-up, one layer at a time**, in the same order you build — never wire
the next layer onto an unverified one:

1. **Tables** — `lemma records create …` + `lemma query run …`; confirm columns and
   RLS scope (`tables.md`).
2. **Files** — upload a real document, wait for `stat` → `COMPLETED`, search a
   phrase from it (`files.md`).
3. **Functions** — `lemma functions run <fn> --data '{…}'` with a realistic
   payload; confirm grants landed (`functions.md`).
4. **Agents** — `lemma agents chat <agent> "…"`; read the **final-answer** message,
   not the first/last (`agents.md`).
5. **Workflows** — `lemma workflows run <wf> --data '{…}'`, submit each FORM,
   inspect `runs get` (`workflows.md`).
6. **Schedules** — fire once with a near-future trigger, confirm the run, then
   **pause test schedules** (`schedules-and-triggers.md`).
7. **Surfaces / app** — send a real message / walk one DESIGN.md scenario in the
   browser (`surfaces.md`, `apps.md`).
8. **The whole thing** — run the success-criteria scenario against seed data and
   confirm the **hero moment** is visible on open.

Put the final scenario, the non-bundled setup (connector auth configs, uploaded
files, seed payloads), and per-layer smoke commands in `README.md` so the next
builder can import, connect, seed, and verify without rediscovering the plan.

## See also

- The resource model this method assembles → `pod-model.md`
- Bundle layout, import/export, cheatsheet → `cli-and-bundles.md`
- Per-resource depth → `tables.md`, `files.md`, `functions.md`, `agents.md`,
  `workflows.md`, `connectors.md`, `schedules-and-triggers.md`, `surfaces.md`,
  `apps.md` (app design-doc → `apps.md` "Plan first")
- Operate a finished pod → the `lemma-user` skill
