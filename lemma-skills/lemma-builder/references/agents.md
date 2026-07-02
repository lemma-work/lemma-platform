# Agents

Agents are LLM workers scoped to a pod: an instruction (system prompt), a set of
toolsets, granted resources, and optional typed input/output. Use agents for
**judgment** — classification, drafting, extraction, research, conversation. Use
**functions** for deterministic work. An agent is the automation layer's "reasoning
worker": it reads the pod's tables and documents, decides, and acts through the same
granted capabilities a function would — but it chooses *when and how* at runtime.

> Grounds in `pod-model.md` (the automation layer). This is the build + CLI view;
> the `lemma-user` skill is the operator view of chatting with and running agents.

## The model, for agents

Like a function, an agent never runs "as itself." The same two pod-model rules govern
everything it can do:

- **Delegated identity.** An agent run is **owned by the user who invoked it** (the
  chatter, the workflow's run identity, the schedule's configured user). Its `POD`
  tools authenticate as that user: RLS tables return only **their** rows, writes are
  stamped with **their** id, `/me/...` is **their** private tree, and a connector
  call goes through **their** connected account. There is no agent-private space and
  no service account — an agent sees exactly what the invoking user would.
- **Zero access by default.** A freshly created agent can touch **nothing** — no
  tables, no folders, no connectors — regardless of what the builder or pod members
  can see. Every resource its tools reach needs an **explicit, name-based grant** in
  `permissions.grants` (a table name, a folder path like `/knowledge`, a connector
  id). Grants are **portable** (no UUIDs), travel in the bundle, and are **replaced**
  on every import. Missing → `MISSING_WORKLOAD_RESOURCE_GRANT` at the tool call,
  naming the resource.

So an agent's real capability surface is *(its toolsets) ∩ (its grants)*, exercised on
the invoking user's behalf. Toolsets say *what kinds of tool* it has; grants say
*which named resources* those tools may touch.

> Scaffold it: `lemma agents init triage` writes `triage.json` + `instruction.md`
> (commented JSONC); `lemma agents grant triage tickets:read,write /knowledge:read`
> fills `permissions.grants`; `lemma agents init triage --runtime <profile-id>` pins
> a runtime. Edit, then import.

## Agent JSON

Bundle shape (folder name **must equal** the agent `name`):

```text
my-pod/agents/triage-agent/
  triage-agent.json
  instruction.md
```

`triage-agent.json`:

```json
{
  "name": "triage-agent",
  "description": "Classifies support tickets by severity and category.",
  "instruction": {"$file": "instruction.md"},
  "toolsets": ["POD", "WEB_SEARCH"],
  "output_schema": {
    "type": "object",
    "properties": {
      "priority": { "type": "string", "enum": ["low", "normal", "high", "urgent"] },
      "category": { "type": "string" },
      "reasoning": { "type": "string" }
    },
    "required": ["priority", "category"]
  },
  "permissions": { "grants": [
    { "resource_type": "datastore_table", "resource_name": "tickets",
      "permission_ids": ["datastore.table.read", "datastore.record.read"] },
    { "resource_type": "folder", "resource_name": "/knowledge",
      "permission_ids": ["folder.read"] }
  ]}
}
```

Optional fields: `input_schema` (typed input when other systems invoke the agent),
`icon_url`, `agent_runtime` (see Runtime Profiles).

## Toolsets

Grant only the toolsets the job needs:

| Toolset | Enables |
| --- | --- |
| `POD` | read/query pod tables and records, read/search pod files, and mint file URLs (in-app member link or a public hit-capped share link) — grant-checked against the agent's own grants |
| `WORKSPACE_CLI` | a sandbox shell with the `lemma` CLI — the most powerful and broadest toolset. Includes `view_image` (vision-gated: silently withheld if the active model has no vision capability) |
| `SKILLS` | loading skills available in the workspace |
| `WEB_SEARCH` | web search |
| `USER_INTERACTION` | ask multiple-choice questions (`ask_user`), show resources/files/tables/widgets (`display_resource`), and gate sensitive actions behind approval (`request_approval`) — behaviors & schemas in `agent-tools.md` |
| `SPEECH` | speak replies and transcribe voice notes (`say` / `listen`) — see `agent-tools.md` |
| `SUBAGENTS` | async sub-agent orchestration — spawn/await/list child conversations, including another instance of itself (see *Agents & Functions as Tools*) |

For pod files and data, prefer `POD` (typed, grant-checked table/record/file tools).
`WORKSPACE_CLI` is the escape hatch when the agent needs a real shell. There is no
separate file-system toolset — file access is part of `POD`, gated by folder grants.

## Using files (search-first → read markdown → page → view image)

This is the single behavior agents get wrong without being told, so spell it out in
the instruction. Pod files are **searchable by path** and **fully readable** — not
snippet-only. The right loop, grounded in the file model (`files.md`):

1. **Search first**, scoped to a folder: `search "refund policy" --scope /knowledge`
   returns ranked chunks **with page numbers**. Folder scope keeps retrieval tight.
2. **Read the converted markdown** of the hit — `files cat <path>` (or
   `download_markdown`) returns the whole document as page-marked markdown. Agents
   that only ever search assume they get snippets; tell them they can read the full
   doc.
3. **Slice by page** for a long doc — `files cat <path> --pages 3-7` over the
   converted markdown.
4. **View a page as an image** when layout/figures/signatures matter — fetch the
   rendered page JPEG child (`…/<doc>/pages/page_0003.jpg`) and use the view-image
   capability to actually *see* it. "What does page 3 *say*?" → markdown;
   "what does it *look* like?" → the page JPEG.

Grant each folder the agent reads (`folder.read`; add `folder.write` for uploads);
`/me` is the invoking user's own tree and needs no grant.

## Using connectors (delegated account)

An agent calls a third-party app through a **granted connector** exercised on the
**invoking user's connected account** — it never holds raw credentials. Grant the
connector (`resource_type: "connector"`, `resource_name: "<connector-id>"`,
`permission_ids: ["connector.use"]`), and either give the agent `POD`-level connector
tools or `WORKSPACE_CLI` so it can run `lemma connectors operations …`. The backend
resolves the configured fixed account or the invoking user's account from the
workload token; if that user has no connected account the call fails with an
account-resolution error. Discover operation ids/payloads before relying on them —
see `connectors.md`.

## Writing instructions

The instruction is the agent's whole worldview. Include:

1. **Role and scope** — what it is and is not responsible for.
2. **The pod's resources by name** — which tables to read/write, which folders hold
   which knowledge (`/knowledge`, `/contracts` — shared paths have **no** `/pod`
   prefix; personal is `/me`), which workflows/functions exist. Agents don't discover
   this reliably on their own.
3. **How to use files** — say explicitly that pod files are searchable by path and
   fully readable via converted markdown (the search-first loop above), or the agent
   assumes snippet-search only. If it should hand a user a link to a file, tell it to
   call the file-URL tool (`url_type="app"` for a signed-in member, `url_type="public"`
   to email/message someone outside the pod).
4. **Output expectations** — when the agent feeds a workflow or another system, define
   the exact fields (and set `output_schema` to enforce it).
5. **Boundaries** — what it must never do (e.g. "never email customers directly; write
   a draft to the table").

Right-size the agent (pod-model heuristic #1). One agent should do everything a single
cohesive judgment needs and return **rich multi-field JSON** (`output_schema`) — that
beats chaining agent→agent, which is slower, costlier, and harder to test. Split into a
*second* agent only when the work is a genuinely **orthogonal** judgment (e.g. triage
vs. reply-drafting are separable concerns, not two halves of one decision) — then each
stays independently testable and grantable. When unsure, start with one rich agent.

## Workload grants

**A newly created agent can access nothing.** Like functions, agents are workload
principals with zero default access — no tables, no files/folders, no connectors, no
matter what the builder or pod members can see. Grant every resource the agent needs
explicitly, or its tool calls fail at runtime.

Grants are **name-based** and **portable**:

| `resource_type` | `resource_name` is… | example |
| --- | --- | --- |
| `datastore_table` | the table name | `tickets` |
| `folder` | the **stored folder path, no prefix** | `/knowledge` |
| `connector` | the connector id | `gmail` |
| `function` | a function name (exposes it as a tool) | `save_expense` |
| `agent` | another agent's name (exposes it as a tool) | `triage-agent` |

They round-trip in bundles: export embeds the agent's current grants under
`permissions.grants`, and import **replaces** the agent's grants with that list on
every upsert — deleting a grant from the JSON and re-importing revokes it. Name
resolution happens against the target pod, so grants port across pods with the bundle.
Or manage them directly:

```bash
lemma agents grant triage-agent tickets:read,write /knowledge:read connector:gmail:use
lemma agents permissions replace triage-agent --file payloads/triage-agent.permissions.json
lemma agents permissions get triage-agent
```

`MISSING_WORKLOAD_RESOURCE_GRANT` in a chat/run = grant the named resource to this
agent. (Folder grants **cascade**: granting `/knowledge` covers everything beneath it.)

## Agents & Functions as Tools

Beyond the built-in toolsets, an agent can call **other agents** and **functions** in
the same pod as tools. This is how you compose specialists — a coordinator that
delegates to a `triage-agent` and a `reply-drafter` — or let an agent run
deterministic logic mid-conversation. This is also why you **rarely wrap an agent in a
function**: an agent is first-class — call it directly, grant it as an `agent_<name>`
tool, or drop it into a workflow AGENT node (pod-model heuristic #6). There are two
complementary mechanisms:

1. **Grant-based one-shot tools** (no toolset) — granting `agent.execute` (for agents)
   or `function.execute` (for functions) on the parent gives it a synchronous
   `agent_<name>` / `function_<name>` tool. The parent calls it, waits, and gets the
   result back. Best for "delegate this, give me the answer." **One grant per tool** —
   see the callout below.
2. **The `SUBAGENTS` toolset** — async orchestration: spawn one or more child
   conversations (including another instance of *itself*), let them run in the
   background, and await/poll/list them. Best for fan-out and long-running sub-tasks.
   Opt in by adding `"SUBAGENTS"` to the parent's `toolsets`.

| Grant the parent agent… | Tool it gains | Tool name | A call does |
| --- | --- | --- | --- |
| `function.execute` on `resource_type: "function"` | that function | `function_<name>` | Runs the function (args = the function's input schema). `API` returns its result inline; `JOB` is awaited, then returns its result. `function.execute` implies `function.read`, so this one grant covers both discovery and execution. The function runs under **its own** grants — you do **not** mirror them onto the parent (see the callout). |
| `agent.execute` on `resource_type: "agent"` | that agent | `agent_<name>` | Spawns a real, persisted **child conversation** (linked via `parent_id`/`parent_run_id`), runs it, and returns its output. Schema-flexible (see below): args = the child's `input_schema` if set, else a single `input` string; result = the child's `output_schema` dict if set, else a plain string. |

In a bundle these are ordinary name-based grants on the **parent** agent:

```json
{
  "name": "coordinator",
  "instruction": {"$file": "instruction.md"},
  "toolsets": ["WEB_SEARCH"],
  "permissions": { "grants": [
    { "resource_type": "function", "resource_name": "save_expense",
      "permission_ids": ["function.execute"] },
    { "resource_type": "agent", "resource_name": "triage-agent",
      "permission_ids": ["agent.execute"] }
  ]}
}
```

Or directly: `lemma agents permissions replace coordinator --file payloads/coordinator.permissions.json`.

> **Function tool = one grant.** Exposing a function as an agent tool needs exactly
> **`function.execute`** on the parent (`function.execute` implies `function.read`, so
> you don't list read separately). The function runs under **its own** FUNCTION
> principal with **its own** grants — the same identity it has when run directly or as
> a job. You do **not** mirror the function's table/file/connector grants onto the
> parent; grant those to the **function**. See `authorization-model.md` §6.
>
> The `agent:`/`function:` shorthand exists:
> ```bash
> lemma agents grant coordinator function:save_expense:execute agent:triage-agent:execute
> lemma pods import ./my-pod/agents/coordinator   # re-import so grants take effect
> ```

**Make the callee tool-friendly.** Schemas are optional but shape the tool:

- A function already declares input via its `code.py` header models — nothing extra.
- A **plain agent** (no schemas) works out of the box as a clean **string-in /
  string-out** tool: the parent passes one `input` string and gets the child's final
  answer back as a string. Good for "ask this specialist a question."
- Add an **`input_schema`** when you want the parent to pass *structured* arguments,
  and an **`output_schema`** when you want a *structured* result back (a dict the
  parent can route on) instead of prose. Set both for a typed tool; set neither for
  the simple string tool.
- Name agents/functions so the tool name reads well in the parent's tool list
  (`agent_triage_agent`, `function_save_expense`). Mention the available helper tools
  in the parent's instruction — agents call them far more reliably when told they
  exist.

**Behavior & limits:**

- The callee runs **under its own grants** — a chain of zero-default-access
  principals, each still delegated to the same invoking user. The function/child agent
  needs its own grants for the tables/files/connectors it touches, so a
  `MISSING_WORKLOAD_RESOURCE_GRANT` can originate from the callee, not the caller.
  Grant those resources to the **callee**, not the parent.
- Both `function_<name>` and `agent_<name>` follow this rule: the parent needs only
  the one `.execute` grant to *trigger* the tool; the callee's own grants govern what
  it can *touch*. There is no grant mirroring in either direction. See
  `authorization-model.md` §6–§7.
- The grant-based `agent_<name>` tool **cannot target the calling agent itself**
  (self-reference is dropped). To run another instance of yourself, use the
  `SUBAGENTS` toolset's self-spawn (below).
- `agent_<name>` is **synchronous from the model's view** — the parent waits for the
  child run to finish (bounded). A child run that exceeds the wait returns a handle
  (`conversation_id`/`run_id`) instead of blocking forever; the parent can keep going
  and poll it.
- Child conversations **inherit the parent's workspace cwd** — a sub-agent works in
  the same directory as its parent rather than a fresh one.
- Use `JOB` functions for long deterministic work (awaited to completion when called
  as a tool) and `API` for quick request/response.

**The `SUBAGENTS` toolset — async orchestration.** Add `"SUBAGENTS"` to a top-level
agent's `toolsets` to give it control tools for running child conversations
concurrently:

| Tool | Does |
| --- | --- |
| `spawn_subagent` | Start a child conversation and return its `conversation_id`/`run_id` immediately (non-blocking). Omit `agent_name` to spawn **another instance of yourself**; pass a name (requires `agent.execute` on it) to spawn a different agent. |
| `await_subagent` | Block (bounded) on a spawned child until it finishes; returns its output. |
| `get_subagent_messages` / `send_subagent_message` | Read a child's transcript / send it a follow-up. |
| `list_subagents` | List the children this conversation spawned, with status. |
| `stop_subagent` | Cancel a running child. |

- **Self-spawn** needs no grant (running another copy of yourself is no privilege
  escalation); spawning a *named other* agent is grant-gated exactly like
  `agent_<name>`.
- **Depth = 1 (enforced):** a spawned sub-agent conversation gets **no** spawning
  tools — neither `SUBAGENTS` nor `agent_<name>` — so sub-agents can't recurse into a
  tree. `function_<name>` tools still work in a sub-agent.

**Tools vs. workflow nodes — pick the composition layer.** A grant lets the LLM
**decide at runtime** whether/when to call a helper (open-ended, judgment-driven
delegation). A **workflow** (AGENT/FUNCTION nodes) is a **fixed graph you control** —
deterministic order, durable state, human FORM steps, retries. Use tools for "let the
agent decide who to ask"; use a workflow for "this exact sequence must run every
time." See `workflows.md`. Either way, compose **orthogonal** specialists — don't split
one cohesive judgment across tools or nodes just to have more of them (heuristic #1).

## Output schema (for workflow & tool consumption)

When an agent feeds a workflow node, another agent, or any system that routes on its
result, give it an **`output_schema`**. An `AGENT` workflow node lands the agent's
output in the run context under the node id; DECISION/FUNCTION nodes downstream read
fields from it with JMESPath — and that only works if the shape is a contract, not
free prose. **Without a schema the agent returns a plain string** — it may *emit*
JSON-looking text, but nothing parses or enforces it, so downstream mappings against
that output are guesswork. Same for `agent_<name>` tools: an `output_schema` makes the
parent get a structured dict back instead of a string. Define the exact fields and `required` set, and test
the agent standalone so its output conforms before wiring it in.

## Runtime profiles

By default agents run on the platform's system runtime. `agent_runtime: {"profile_id":
"..."}` can pin an org-level runtime profile (daemon-backed harness, OpenAI-compatible,
or Anthropic-compatible endpoint — managed via `lemma runtime profiles`). Leave it
unset unless the pod has a specific requirement.

## Patterns

- **Read-only classifier.** `POD` toolset + `tickets:read` + `/knowledge:read`, an
  `output_schema` of `{priority, category}`. Feeds a workflow DECISION. Grant no
  write; the agent only judges.
- **Document analyst.** `POD` + a knowledge folder grant; instruction tells it to
  search-first, read converted markdown, and view page JPEGs for figures. Returns a
  structured extraction for a function to persist.
- **Coordinator.** `WEB_SEARCH` + `agent.execute` on two **orthogonal** specialists (+
  optional `SUBAGENTS` for fan-out). Composes genuinely separable judgments — reach for
  it when the sub-tasks are distinct concerns, not to split one decision into a chain.
- **Action agent.** A granted connector + a draft-to-table boundary in the
  instruction ("write the reply to `drafts`, never send directly") — judgment plus a
  delegated, audited side effect.

## Test loop

```bash
lemma pods import ./my-pod/agents/triage-agent --dry-run
lemma pods import ./my-pod/agents/triage-agent

lemma agents chat triage-agent "Classify: 'My payment was charged twice', cite fields used"
lemma agents run triage-agent "Classify this ticket: ..."     # waits + streams the result by default
lemma agents run triage-agent "..." --no-wait                 # start detached; prints the conversation id

# Each agent run IS a conversation. `conversations` is the run surface:
lemma conversations list --agent triage-agent   # this agent's runs
lemma conversations get <conversation-id>        # run state + messages
lemma conversations send <conversation-id> "..." # continue the run
lemma conversations stream <conversation-id>     # attach to an in-flight run
```

Check: instruction following, **data-access boundaries** (does it read the right
table, and *only* its granted rows?), output-schema conformance, and that it doesn't
perform writes you didn't intend. Because the run is delegated, test as a normal
member to confirm RLS scoping looks right from a user's seat.

## Limits & gotchas

- **Zero default access.** No grants → the agent can do nothing; a tool call fails
  with `MISSING_WORKLOAD_RESOURCE_GRANT` naming the resource. Grants are replaced
  wholesale on import.
- **Delegated, not elevated.** An agent cannot see another user's RLS rows or another
  user's `/me`, and cannot use `mode=ADMIN` — it runs as the invoking user. Cross-user
  reads are an admin/app concern, not an agent default.
- Agent `name` is immutable through upsert (it's the match key). Everything else
  updates.
- **Output schema is the contract for downstream consumption** — without it, workflow
  JMESPath mappings and typed tool results are guesswork.
- Don't use an agent to store state ("remember that X") — write to tables. Conversation
  history is per conversation; a workflow `AGENT` node starts fresh each run.
- **`view_image` is vision-gated.** An agent with `WORKSPACE_CLI` gets `view_image`
  only if the runtime model supports vision. Non-vision models run as if the tool doesn't
  exist. If page-image analysis is required, pin a vision-capable runtime profile.
- **Runtime model affects tool availability.** Some tools are withheld per model
  capability (vision above; speech `say`/`listen` always need `SPEECH` in `toolsets`).
  Test with the actual runtime profile the pod will use in production.

## Verify

```bash
lemma agents get triage-agent                 # config landed
lemma agents permissions get triage-agent     # grants present
lemma agents chat triage-agent "<realistic prompt>"   # behavior + access check
```

## See also

- The model → `pod-model.md` · deterministic helpers it calls → `functions.md`
- Data it reads/writes → `tables.md` · documents/RAG it reads → `files.md`
- External apps it acts through → `connectors.md` · orchestrating it in a graph →
  `workflows.md`
- Agent chat inside a browser app → `apps.md` (+ `app-recipes/agent-chat.md`)
- How its interaction/voice/approval tools behave (ask_user, display_resource,
  request_approval, say/listen) + UI round-trip → `agent-tools.md`
- Operate/chat with an existing pod's agents → the `lemma-user` skill
