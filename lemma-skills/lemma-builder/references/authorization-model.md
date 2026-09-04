# Authorization model

How a pod decides what a workload (agent / function / workflow) may do. Read this
when a call hits a 403 or you're wiring grants. The one-line version: **named
workloads start with zero access and act on the intersection of what they're granted
and what the person who invoked them could do; deletes and other destructive actions
additionally need an explicit grant or a live user approval.**

## §1 Two ledgers

Authorization reads two ledgers, and a delegated call has to satisfy **both**:

1. **Human roles** — pod members are `VIEWER` < `USER` < `EDITOR` < `ADMIN`. Roles gate
   member-facing actions in the app/CLI, **and they are the ceiling on anything a
   workload does for that member** (§2).
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

## §2 Delegated identity + the intersection rule

When a function or agent runs, it acts **as the user who invoked it** — not a service
account. Row-level security and the personal `/me` area always resolve to *that* user;
a workload never sees more rows than the invoker would.

**The intersection rule.** A named workload may perform an action only when **both**
halves hold:

1. the **workload** holds an explicit grant for it (or the invoker approved that action
   for the session, §3), **and**
2. the **invoking person** could perform the same action on the same resource
   themselves — the ordinary role/ownership/visibility evaluation, run for them.

A grant is therefore a **ceiling on the workload, never a promotion for the person
driving it**. A `POD_VIEWER` who chats with an agent granted `datastore.record.write`
still cannot write through it; the refusal is `DELEGATION_EXCEEDS_INVOKER`, and the fix
is the *person's* role, not more grants. It is also what makes a delegation expire with
its person: someone removed from the pod holds nothing, so the intersection is empty on
their very next request. (Ground truth:
`lemma-backend/app/core/authorization/workload_authority.py`.)

**Headless runs** — a run with no invoking person — are authorized on the workload's
grants alone, because there is no second set to intersect with. In practice almost
nothing is person-less: a `TIME`, `WEBHOOK`, or `DATASTORE` fire runs as a real person
(the schedule's configured user, or the changed row's owner — see
`schedules-and-triggers.md`), so **the ceiling applies to automation too**. Design for
it: a schedule owned by a viewer is a schedule that cannot write.

Two consequences worth designing around:

- **Grants are still mandatory.** Being an admin does not lend a workload access — the
  workload's own grant half is unchanged, and a missing grant is still
  `MISSING_WORKLOAD_RESOURCE_GRANT`.
- **The person's seat is now part of the test.** Test an agent as a normal member and
  as the member who will actually invoke it in production, not only as yourself.

The **default pod agent** (the pod's built-in assistant) reaches the same place by a
different route: it holds no grants of its own and *mirrors* the invoking user's pod
permissions, so it is already bounded by them. It is still subject to the destructive
gate (§3).

## §3 Destructive actions & approvals

No workload — the default pod agent included — performs a **destructive** action by
default. Destructive = `pod.delete`, `pod.role.manage`, `pod.member.manage`,
`datastore.table.delete`, `folder.delete`, `function.delete`, `agent.delete`,
`workflow.delete`, `app.delete`, `schedule.delete`, `connector_account.manage`.
(Row deletes via `datastore.record.write` and file deletes via `folder.write` are
**not** destructive — routine automation, RLS-scoped.)

Two ways to unlock a destructive action:

- **Explicit grant** — put the destructive permission in the workload's
  `permissions.grants`. This is **standing authority**: it needs nobody watching, so
  it's the path for schedules, webhooks, and workflow runs that nobody is sitting in
  front of. Import and `doctor` flag these as advisories (a workload that can delete
  without a prompt). It still does not lift the §2 ceiling — the person the run belongs
  to must be able to do it too.
- **Session approval** — when a workload hits the gate mid-conversation it can call
  `request_approval`, and the user picks:
  - **Approve once** — the wrapped action runs one time (as the user). The next
    attempt re-prompts.
  - **Approve for session** — the action *type* stays approved for **that agent in
    that conversation** for a bounded window (default 1 hour). A cleanup agent
    deleting five tables asks once, not five times.

Because the default pod agent holds no grants, destructive actions from it always route
through approval — there is no "standing authority" path for it.

**An approval unlocks the gate; it confers no authority.** Passing the destructive gate
only means the workload may *attempt* the action — the §2 intersection still applies
afterwards, so a person cannot approve, for a workload, something they could not do
themselves. An `EDITOR` who approves `pod.member.manage` gets
`DELEGATION_EXCEEDS_INVOKER`, not a member change.

## §4 The 403 decoder

Deny codes come back verbatim in the error `code`. Map each to the fix:

| Code | Meaning | Fix |
| --- | --- | --- |
| `MISSING_WORKLOAD_RESOURCE_GRANT` | The **workload** half failed: no grant for the resource it touched. | Grant it: `lemma agents grant <name> <spec>` or add to `permissions.grants`. The message names the resource. |
| `DELEGATION_EXCEEDS_INVOKER` | The **person** half failed: the workload holds the grant, the person it is acting for does not hold the action. | Fix the **invoker's** role or the resource's visibility. More grants will not help — this is deliberately reported separately so you don't chase the wrong half. |
| `DESTRUCTIVE_ACTION_REQUIRES_APPROVAL` | A delete/manage action with no destructive grant and no session approval. | Grant the destructive permission (headless) **or** have the user approve for session. Clearing this gate still leaves the two halves above to satisfy. |
| `INSUFFICIENT_PERMISSION` | An **org-scoped** resource (no pod) the invoking user's role can't reach — workload grants are pod rows and have nothing to say here. | Fix the member's role — not a workload-grant problem. |
| `DELEGATION_SCOPE_VIOLATION` | A minimal-scope token (e.g. a function tool scoped to `function.execute`) was used for an unrelated action. | Usually a bug in how the tool is wired, not a grant to add. |
| `PERSONAL_RESOURCE_DENIED` | Another user's PERSONAL resource — privacy trumps grants; no grant unlocks it. | Don't target other users' private resources from a workload. |
| `POD_SCOPE_MISMATCH` / `ORG_SCOPE_MISMATCH` | The resource lives in a different pod/org than the run. | A cross-pod reference that shouldn't exist — fix the name or the target. |

Allow reasons you may see in logs: `POD_VISIBLE`, `WORKLOAD_RESOURCE_GRANT`,
`RESOURCE_OWNER`, `PUBLIC_RESOURCE` (a grant matched, named by the resource's
visibility), `ORG_VISIBLE` (an org resource the person's role reached), and
`SESSION_APPROVAL` (an approve-for-session decision stood in for the grant).

## §4b Authoring grants — the full vocabulary

Two commands write grants, sharing one spec grammar:

```bash
lemma agents grant <name> <spec>...             # edits the BUNDLE file (permissions.grants)
lemma agents permissions add|remove <name> <spec>...   # edits a LIVE pod (read → merge → replace)
```

Same for `lemma functions …`. A spec is `name:perms` (type inferred: a leading
`/` means a folder, else a table) or `type:name:perms`. `perms` is comma-separated
friendly verbs, or raw permission ids when you need one the presets don't cover.

| Spec | Grant it produces | Use it for |
| --- | --- | --- |
| `tickets:read` | `datastore_table` → `datastore.table.read`, `datastore.record.read` | read a table |
| `tickets:read,write` | + `datastore.record.write` | read + write rows (row *delete* rides on write) |
| `tickets:alter` / `tickets:delete` | `datastore.table.update` / `.delete` | change or drop the table itself (**delete is destructive**) |
| `/knowledge:read` | `folder` → `folder.read` | a shared folder **and everything beneath it** |
| `/me/drafts:write` | `folder` → `folder.write` | the invoking user's private area |
| `doc:/contracts/msa.pdf:read` | `document` → `folder.read` | one specific file |
| `connector:gmail:use` | `connector` → `connector.use` | a connector, **user-resolved** (see below) |
| `account:<account-id>:use` | `connector_account` → `connector_account.use` | a connector account, **pinned/fixed** (see below) |
| `function:score_ticket:execute` | `function` → `function.execute` | expose a function as a tool (§6) |
| `agent:researcher:execute` | `agent` → `agent.execute` | dispatch another agent (§7) |
| `workflow:intake:execute` | `workflow` → `workflow.execute` | start a workflow |
| `schedule:nightly:read` / `:write` | `schedule` → `schedule.read` / `.update` | inspect or retarget a schedule |
| `app:dashboard:read` / `:write` / `:publish` | `app` → `app.*` | a Lemma **app** in this pod |

> `app:<name>:use` is the pre-rename spelling of a **connector** grant and still
> works, with a note. Write `connector:<name>:use`. Bare `app:` now means a Lemma app.

### Connector accounts: user-resolved vs fixed

This is the distinction §8 names, in grant form:

**User-resolved (the default, and what you want most of the time).** One grant.
Each invoker's own connected account is used, so the same agent reads *your*
mail for you and *mine* for me.

```json
{ "resource_type": "connector", "resource_name": "gmail",
  "permission_ids": ["connector.use"] }
```

**Fixed account (a pinned shared identity).** Two grants — the connector *and*
the specific account. Every invoker now acts through that one account, which is
how a "support@" sender works, and how a schedule keeps sending after the person
who set it up stops reading that mailbox.

```json
{ "resource_type": "connector", "resource_name": "gmail",
  "permission_ids": ["connector.use"] },
{ "resource_type": "connector_account", "resource_name": "<account-id>",
  "permission_ids": ["connector_account.use"] }
```

An account has no human-facing name, so the grant carries its **id** — which is
specific to the org that issued it. Export therefore replaces it with a
`${…}` variable recorded in `pod.json` (with the connector and kind to reconnect),
and import resolves it from `--var`. Unsupplied, the grant is **dropped with a
warning** and the workload falls back to user-resolved mode; wire it up after
import with `lemma agents permissions add <name> account:<id>:use`. Get the id
from `lemma connectors overview`.

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

The chain does not widen the ceiling: every link is still delegated to the **same
invoking person**, so a callee's §2 half is that person's access, not the parent
agent's. A `DELEGATION_EXCEEDS_INVOKER` from deep in a chain is still about the human
who started it.

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
  connector and `connector_account.use` on the account. It then works whoever
  triggered it — the classic shared-sender setup — as long as that person clears the
  §2 ceiling, i.e. is a `POD_USER` or above (both `connector.use` and
  `connector_account.use` are `POD_USER` permissions; a `POD_VIEWER` holds neither and
  gets `DELEGATION_EXCEEDS_INVOKER`).

`connector_account.manage` is destructive (§3); plain `connector_account.use` is not.
See `connectors.md` for the payload.

## §9 Import / export grant semantics

- Grants **travel with the bundle**: export always embeds each workload's
  `permissions.grants`, and import **replaces** them on every upsert (the bundle is
  the source of truth for what a workload may access).
- **`permissions` present vs absent is a real distinction.** A block — even
  `{"grants": []}` — replaces the workload's grants with exactly that list. Omitting
  the key entirely leaves the existing grants alone. So a partial, hand-written
  bundle can't silently strip access, and a full export stays declarative.
- **Creating a workload with no grants is advised, loudly.** It imports fine and then
  403s the first time it touches anything, which is the single most common way a new
  pod arrives broken. `import`, `import --dry-run`, and `lemma pods doctor` all say so.
- The **deferred permissions pass** applies all grants *after* every resource exists,
  so agent/function/table/folder/app cross-references resolve. Both importers (the CLI
  and the backend's async job) defer both agents and functions.
- A grant referencing a table/function/agent/workflow/schedule/app/folder the bundle
  neither creates nor finds in the pod is a **hard failure** (import aborts before
  writing). A `connector_account` grant naming an unreachable account is too.
- **Plain `connector` grants are environment-specific** — the connected account lives
  in the target org, not the bundle; import surfaces them as advisories.
- **Destructive grants** import fine but are advised (standing authority, no prompt).

Verify after import — this is one command, not a guess:

```bash
lemma functions permissions get <name>
lemma pods doctor              # zero-grant workloads, dangling + dead-account grants
```
