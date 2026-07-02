# Authorization model

How a pod decides what a workload (agent / function / workflow) may do. Read this
when a call hits a 403 or you're wiring grants. The one-line version: **named
workloads start with zero access and act on exactly what they're granted; deletes and
other destructive actions need an explicit grant or a live user approval.**

## §1 Two ledgers

Authorization is two separate ledgers that never mix:

1. **Human roles** — pod members are `VIEWER` < `USER` < `EDITOR` < `ADMIN`. Roles gate
   member-facing actions in the app/CLI.
2. **Workload grants** — a named agent/function/workflow starts with **zero** access
   and holds a list of explicit resource grants. Grants are **name-based** (a table
   name, a folder path, a connector id, another agent/function name), so they export
   and re-import into any pod.

A grant is a `{resource_type, resource_name, permission_ids}` object living in the
workload's `permissions.grants`:

```json
{
  "name": "triage",
  "permissions": { "grants": [
    { "resource_type": "datastore_table", "resource_name": "tickets",
      "permission_ids": ["datastore.table.read", "datastore.record.read", "datastore.record.write"] },
    { "resource_type": "folder", "resource_name": "/knowledge",
      "permission_ids": ["folder.read"] },
    { "resource_type": "connector", "resource_name": "gmail",
      "permission_ids": ["connector.use"] }
  ]}
}
```

## §2 Delegated identity + the grant-first rule

When a function or agent runs, it acts **as the user who invoked it** — not a service
account. Row-level security and the personal `/me` area always resolve to *that* user;
a workload never sees more rows than the invoker would.

**Grant-first**: a named workload may perform exactly the actions for which the
workload itself holds an explicit grant — inside its own pod. The invoking user's role
is **not** also required. The user's identity is consulted only for owner checks
(PERSONAL resources, connector-account ownership) and data-layer scoping (RLS, `/me`).

The **default pod agent** (the pod's built-in assistant, no user-created Agent entity)
is the exception: it *mirrors the invoking user's* pod permissions and holds no grants
of its own — but it is still subject to the destructive gate (§3).

## §3 Destructive actions & approvals

No workload — the default pod agent included — performs a **destructive** action by
default. Destructive = `pod.delete`, `pod.role.manage`, `pod.member.manage`,
`datastore.table.delete`, `folder.delete`, `function.delete`, `agent.delete`,
`workflow.delete`, `app.delete`, `schedule.delete`, `connector_account.manage`.
(Row deletes via `datastore.record.write` and file deletes via `folder.write` are
**not** destructive — routine automation, RLS-scoped.)

Two ways to unlock a destructive action:

- **Explicit grant** — put the destructive permission in the workload's
  `permissions.grants`. This is **standing authority**: it works with no human present,
  so it's the path for headless schedules, webhooks, and workflow runs. Import and
  `doctor` flag these as advisories (a workload that can delete without a prompt).
- **Session approval** — when a workload hits the gate mid-conversation it can call
  `request_approval`, and the user picks:
  - **Approve once** — the wrapped action runs one time (as the user). The next
    attempt re-prompts.
  - **Approve for session** — the action *type* stays approved for **that agent in
    that conversation** for a bounded window (default 1 hour). A cleanup agent
    deleting five tables asks once, not five times.

Because the default pod agent holds no grants, destructive actions from it always route
through approval — there is no "standing authority" path for it.

## §4 The 403 decoder

Deny codes come back verbatim in the error `code`. Map each to the fix:

| Code | Meaning | Fix |
| --- | --- | --- |
| `MISSING_WORKLOAD_RESOURCE_GRANT` | The workload lacks a grant for the resource it touched. | Grant it: `lemma agents grant <name> <spec>` or add to `permissions.grants`. The message names the resource. |
| `DESTRUCTIVE_ACTION_REQUIRES_APPROVAL` | A delete/manage action with no destructive grant and no session approval. | Grant the destructive permission (headless) **or** have the user approve for session. |
| `INSUFFICIENT_PERMISSION` | A **human role** gap (or an org-scoped resource the invoking user can't reach). | Fix the member's role — not a workload-grant problem. |
| `DELEGATION_SCOPE_VIOLATION` | A minimal-scope token (e.g. a function tool scoped to `function.execute`) was used for an unrelated action. | Usually a bug in how the tool is wired, not a grant to add. |
| `PERSONAL_RESOURCE_DENIED` | Another user's PERSONAL resource — privacy trumps grants; no grant unlocks it. | Don't target other users' private resources from a workload. |

Allow reasons you may see in logs: `POD_VISIBLE` / `WORKLOAD_RESOURCE_GRANT` (grant
matched), `SESSION_APPROVAL` (an approve-for-session decision covered it).

## §5 Permission implications

Some permissions imply weaker ones, so you don't list both:

- **`execute ⊃ read`** for agents, functions, and workflows — `function.execute` alone
  lets a workload both discover and run the function; you never also grant
  `function.read`.
- **write/delete ⊃ read** within the table, folder, and app families.

Redundant ids in an exported bundle (e.g. both read and execute) are harmless — they
just aren't necessary.

## §6 Function as an agent's tool

Grant an agent **`function.execute`** on a function and it gains a `function_<name>`
tool. That is the **only** grant needed:

```json
{ "resource_type": "function", "resource_name": "score_ticket",
  "permission_ids": ["function.execute"] }
```

The function runs under **its own** FUNCTION principal with **its own** grants — the
same identity as when it's run directly or as a job. You grant the tables / files /
connectors it touches to the **function**, never mirrored onto the parent agent. A
`MISSING_WORKLOAD_RESOURCE_GRANT` from a tool call names what the *function* lacks —
fix it on the function.

## §7 Agent as a tool + sub-agents

Grant an agent **`agent.execute`** on another agent and it gains an `agent_<name>` tool
that spawns a child conversation and returns its output — again the child runs under
its own grants. The `SUBAGENTS` toolset lets an agent spawn copies of **itself** with
no agent grant at all (self-spawn is grant-free); to fan out to *other* agents it needs
`agent:<other>:execute`. `doctor` warns when a SUBAGENTS agent has no agent grants
("self-spawn only").

## §8 Connector account modes

- **USER-owned (default)** — no `account_id`; the call runs against the invoking user's
  own connected account. `connector.use` on the connector is enough.
- **Pinned shared account (AGENT-owned)** — a fixed `account_id`; every invoker uses
  that one account. Needs **two** grants on the workload: `connector.use` on the
  connector and `connector_account.use` on the account. It then works for every
  invoker, independent of who triggered it — the classic shared-sender setup.

`connector_account.manage` is destructive (§3); plain `connector_account.use` is not.
See `connectors.md` for the payload.

## §9 Import / export grant semantics

- Grants **travel with the bundle**: export embeds each workload's
  `permissions.grants`; import **replaces** them on every upsert (the bundle is the
  source of truth for what a workload may access).
- The **deferred permissions pass** applies all grants *after* every resource exists,
  so agent/function/table cross-references resolve.
- A grant that references a table/function/agent/folder the bundle neither creates nor
  finds in the pod is a **hard failure** (import aborts before writing).
- **Connector grants are environment-specific** — the account lives in the target env,
  not the bundle; import surfaces them as advisories. Wire the account up after import.
- **Destructive grants** import fine but are advised (standing authority, no prompt).
