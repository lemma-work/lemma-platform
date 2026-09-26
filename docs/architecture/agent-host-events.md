# Agent Host run events

What an Agent Host tells Lemma while a local coding agent works, and where
each part of that is decided.

The short version: **the host normalizes, the backend maps.** An ACP adapter
reports tool calls in its own shape. Claude Code names them in
`_meta.claudeCode.toolName`. Codex names an MCP call in `rawInput.server/tool`
and titles everything else. OpenCode puts the name in the first `title` and
then overwrites it. The host knows which adapter and which pinned version it is
running (`desktop/agent-host/agent-adapters.lock.json`), so it is the only
component that can read those shapes with certainty. It turns each one into
the typed events below, and the backend turns those into conversation messages
one for one, with no guessing.

The backend used to do the guessing. It read a tool's name from whichever of
`name`, `tool_name`, `_meta.*.toolName`, `kind` or `title` came first. Every
Codex MCP call therefore became `exec_command`, because Codex reports MCP calls
with `kind: "execute"`. Claude Code's `Bash` never became `exec_command`. And
five separate tool-name normalizers, in Rust, Python, the TypeScript SDK and
two frontends, disagreed with each other. This document replaces all of that
with one contract.

## Where the contract lives

| Side | Code |
|---|---|
| Host: wire types | `desktop/agent-host/src/protocol.rs` (`EventType`, `ToolRef`, `ToolStatus`) |
| Host: ACP to events | `desktop/agent-host/src/normalize/` (one module per adapter) |
| Backend: wire types | `lemma-backend/app/modules/agent/domain/agent_host.py` (`AgentHostEventType`) |
| Backend: events to messages | `lemma-backend/app/modules/agent/infrastructure/harnesses/agent_host/events.py` |
| Shared fixture | `desktop/agent-host/tests/fixtures/wire_contract.json` |
| Ground truth | `desktop/agent-host/tests/fixtures/acp/<adapter>@<version>/*.jsonl` |

`wire_contract.json` is asserted from both sides. Rust checks that its enums
and payload shapes match the fixture, and the backend checks the same fixture
against its models, so the two cannot drift silently.

## Event types

Every event carries `run_id`, `lease_epoch`, a per-run contiguous `sequence`,
a `type`, an optional `object_id`, and a `payload`. Delivery and
de-duplication are described in [agent-host.md](agent-host.md#the-link).

| `type` | `object_id` | Payload | Backend produces |
|---|---|---|---|
| `run_state` | none | `state`, `provider_session_id`, `host_cwd` | session binding (no message) |
| `agent_message_chunk` | segment id | `text` | live `TOKEN` (kind `text`) |
| `agent_message_upsert` | segment id | `text` | durable text, sealed at each boundary |
| `agent_thought_chunk` | segment id | `text` | live `TOKEN` (kind `thinking`) |
| `agent_thought_upsert` | segment id | `text` | durable thinking, flushed per step |
| `tool_call` | call id | [tool call](#tool-calls) | `MESSAGE` tool call, plus a live `tool` token |
| `tool_call_progress` | call id | `text` | live `TOKEN` (kind `tool_output`) |
| `tool_call_result` | call id | [tool result](#tool-results) | `MESSAGE` tool return |
| `usage` | none | [token usage](#usage) | `USAGE` |
| `session_update` | none | `title`, `mode`, `commands`, `context` | `STATUS` |
| `config_update` | none | `kind` plus detail | `STATUS` |
| `permission_request` | request id | [permission](#permission-requests) | `request_approval` call |
| `steer_result` | Lemma message id | [steering](#steering) | marks the message delivered (no message) |
| `terminal` | none | `state`, `error`, `supersedes_stream` | run end |

Removed in this version, because each one was a place where the backend had to
interpret ACP itself:

- `tool_call_upsert` and `tool_call_update` became `tool_call`,
  `tool_call_progress` and `tool_call_result`.
- `plan_upsert` became an `update_plan` tool call (see [Plans](#plans)).
- `usage_update` became `usage` (tokens) and `session_update.context` (window).
- `user_message` is gone. The backend never read it.

### Text

Text is unchanged from the previous version: chunks stream live, and the host
seals what it has buffered into an `*_upsert` before any non-text event and at
64 KiB. Replaying only the upserts rebuilds the exact text. An image or other
rich block still travels as an `agent_message_chunk` whose `content` is the ACP
content block; the backend's artifact writer turns it into a pod file.

Thinking is flushed at every step boundary: a tool call, a permission request,
or the end of the turn. It used to be saved once per run, so a run showed one
"Thought" after all of its tool calls instead of the reasoning before each one.

## Tool calls

The host emits `tool_call` **once per call, when its arguments have settled**,
and never again for that call. A conversation message is appended, never
revised, and a streaming adapter reports a call before the model has finished
writing its input. Claude Code opens with `rawInput: {}`, and OpenCode with an
empty `rawInput` and a placeholder title. So the host holds the call and
releases it when:

- an update arrives carrying no new arguments (the adapter's own "input
  written" signal);
- a permission request for the call arrives (the input is final by then);
- the call closes; or
- the turn ends.

The same hold used to live in the backend (`tool_calls.py`). Moving it to the
host means it can use adapter-specific knowledge. OpenCode, for example, reports
the tool's name only in its *first* title and replaces it with a description
afterwards.

```json
{
  "tool": {
    "name": "exec_command",
    "source": "native",
    "server": null,
    "title": "echo hello-lemma",
    "kind": "execute"
  },
  "input": { "cmd": "echo hello-lemma", "workdir": "/Users/me/lemma/c/…" },
  "parent_call_id": null
}
```

- `tool.name` is from the [canonical vocabulary](#canonical-tools) when the
  host recognises the tool. Otherwise it is the adapter's own name converted to
  `snake_case` (`NotebookEdit` becomes `notebook_edit`).
- `tool.source` says whose tool it is:
  - `native`: the agent's own tool.
  - `lemma`: one of Lemma's MCP tools. `name` has the `lemma_` prefix and every
    namespace removed, so it is the same name the pod agent uses.
  - `mcp`: someone else's MCP server; `server` names it.
- `tool.title` and `tool.kind` are the adapter's own words. They are there for
  display only and nothing branches on them.
- `input` is canonical for canonical tools, and the adapter's arguments
  verbatim otherwise.
- `parent_call_id` is set for calls made inside a sub-agent (Claude Code's
  `Task`), so a client can nest them.

**Pausing tools.** `ask_user`, `request_approval`, `browser_sign_in` and the
wait tools are Lemma tools that Lemma writes to the conversation itself when
the MCP call arrives. The backend therefore drops a `tool_call` and its
`tool_call_result` when `source` is `lemma` and the name is in
`PAUSING_TOOL_NAMES`. Before this version that check matched on a guessed
name, and it never matched a Codex call.

## Tool results

```json
{ "status": "completed", "output": { "exit_code": 0, "stdout": "hello-lemma\n" }, "error": null }
```

- `status` is one of `completed`, `failed`, `cancelled` or `denied`.
- `output` is canonical for canonical tools. For Lemma and third-party MCP tools
  it is the tool's own return value, taken out of the MCP envelope: the
  `structuredContent` if there is one, otherwise a single text block that parses
  as a JSON object, otherwise the content blocks as they came.
- `error` is a sentence for any status other than `completed`: the tool's error,
  `exited with code N`, `not allowed` or `cancelled`.

A `tool_call_result` always follows a `tool_call` with the same `object_id`. If
an adapter closes a call it never opened, the host opens it first. The backend
used to write a return that paired with nothing in that case.

`tool_call_progress` carries live output while the call runs: a terminal's
`terminal_output` delta, or a content block that arrived before completion.
It is not persisted. The durable output is in the result.

## Canonical tools

The same names Lemma's own agent uses. Every card, icon and approval in the
product is keyed on them.

| `name` | `input` | `output` |
|---|---|---|
| `exec_command` | `cmd` (string), `workdir`, `description` | `exit_code`, `stdout`, `stderr` |
| `read_file` | `file_path`, `offset`, `limit` | `content` |
| `write_file` | `file_path`, `content` | `message` |
| `edit_file` | `file_path`, `old_string`, `new_string`, `changes` | `message`, `changes` |
| `delete_file` | `file_path` | `message` |
| `move_file` | `source`, `destination` | `message` |
| `list_files` | `path` | `output` |
| `glob` | `pattern`, `path` | `output` |
| `grep` | `pattern`, `path`, `glob` | `output` |
| `web_search` | `query` | `output` |
| `web_fetch` | `url`, `prompt` | `output` |
| `update_plan` | `todos: [{content, status, priority}]` | `todos` |
| `task` | `description`, `prompt`, `subagent_type` | `output` |

`changes` is a list of `{file_path, kind: add|update|delete, old_text,
new_text}`. It is what an `apply_patch` or a multi-file edit carries instead of
one string replacement. `cmd` is always a string: an argv is shell-joined, and a
`bash -lc '<script>'` wrapper is reduced to the script, because that is the
command a person would recognise.

## Per-adapter mapping

Taken from the recorded transcripts. Change a row only together with a
re-recorded transcript that shows the new behaviour.

### Claude Code (`claude-agent-acp`)

The name is always in `_meta.claudeCode.toolName`, on the `tool_call` and on
every update.

| Adapter name | Canonical | Notes |
|---|---|---|
| `Bash` | `exec_command` | `command` becomes `cmd` |
| `BashOutput`, `KillShell` | `bash_output`, `kill_shell` | native, verbatim |
| `Read` | `read_file` | |
| `Write` | `write_file` | |
| `Edit`, `MultiEdit` | `edit_file` | `edits` becomes `changes` |
| `Glob` | `glob` | |
| `Grep` | `grep` | |
| `WebFetch` | `web_fetch` | |
| `WebSearch` | `web_search` | |
| `Task`, `Agent` | `task` | nested calls carry `parent_call_id` from `_meta.claudeCode.parentToolUseId` |
| `TodoWrite`, `Task*` | none | the adapter reports these only as ACP `plan` updates; see [Plans](#plans) |
| `mcp__lemma_tools__lemma_<tool>` | `<tool>`, source `lemma` | |
| `mcp__<server>__<tool>` | `<tool>`, source `mcp` | |

A result's `rawOutput` is the Anthropic tool-result content: a string for
`Bash`, a list of content blocks for MCP tools.

### Codex (`codex-acp`)

Codex names nothing except MCP calls. The rest is read from `kind`, `title`,
`rawInput`, `locations` and diff content.

| Adapter shape | Canonical | Notes |
|---|---|---|
| `_meta.is_mcp_tool_call` with `rawInput.server`/`tool` | `<tool>` (lemma or mcp) | arguments in `rawInput.arguments` |
| `kind: execute`, `rawInput.command` | `exec_command` | output in `rawOutput.formatted_output`, `exit_code` |
| `kind: edit` with `diff` content | `edit_file` | `changes` from the diff blocks; `_meta.kind` gives add or update |
| `kind: read`, title `Read file '…'` | `read_file` | path from `locations` |
| `kind: read`, title `List files` | `list_files` | |
| `kind: search`, `rawInput.type: webSearch` | `web_search` | the query is only in the *closing* update |
| `kind: search`, title `Search for '…'` | `grep` | pattern parsed from the title |
| `kind: search`, `fuzzyFileSearch.<session>` | `glob` | the id is reused, so the host appends the sequence to keep calls distinct |

### OpenCode (`opencode acp`)

The tool's name is the `title` of the *first* `tool_call`. Later updates
overwrite `title` with a description, so the host keeps the first one.

| First title | Canonical | Notes |
|---|---|---|
| `bash` | `exec_command` | output in `rawOutput.metadata.exit`, `rawOutput.output` |
| `read` | `read_file` | `filePath` becomes `file_path` |
| `write` | `write_file` | |
| `edit` | `edit_file` | `oldString`/`newString` |
| `glob` | `glob` | |
| `grep` | `grep` | |
| `list` | `list_files` | |
| `webfetch` | `web_fetch` | |
| `todowrite` | `update_plan` | |
| `lemma_tools_lemma_<tool>` | `<tool>`, source `lemma` | OpenCode joins server and tool with `_` |
| any name with `_`, or none of the above | verbatim, source `mcp`, no server | OpenCode's own tools are single words; an underscore is its join of a third-party MCP server and tool, which cannot be split without knowing the server. Reported as `mcp` so no Lemma card claims it — a server `web` with a tool `search` arrives as `web_search`, which as `native` was drawn as Lemma's web-search card over a payload it had never seen |

### Cursor (`cursor-agent acp`)

There is no recorded transcript yet: Cursor is not installed where the others
were recorded. The generic ACP rules below apply until one is. Record it before
changing them.

### Any other adapter

`kind` and `locations` are the only ACP-standard fields, so an unknown adapter
gets:

- `execute` becomes `exec_command`;
- `read` becomes `read_file` with a path from `locations`;
- `edit` becomes `edit_file`;
- `delete` and `move` become `delete_file` and `move_file`;
- `fetch` becomes `web_fetch`;
- `search`, `think`, `switch_mode` and `other` keep the adapter's title,
  converted to `snake_case`.

## Plans

An agent's to-do list is always delivered as an `update_plan` tool call with
`input.todos` and a matching result, because the product already renders that
as the plan card. Three adapter shapes reach it:

- **Claude Code** sends ACP `plan` updates (from `TodoWrite` and `Task*`). The
  host synthesizes a `tool_call` and a `tool_call_result`, with call id
  `plan-<sequence>`, for each one.
- **OpenCode** sends a `todowrite` tool call, which is mapped directly.
- **Codex** has no plan tool over ACP.

## Usage

```json
{ "input_tokens": 1217, "output_tokens": 5, "cached_input_tokens": 20224, "reasoning_tokens": 0, "total_tokens": 21446 }
```

Emitted **once per turn**, from the token usage on the adapter's `session/prompt`
response (Codex and OpenCode send it, and so does any adapter implementing ACP's
end-of-turn usage). ACP's `usage_update` notification is not token usage: it is
the context window (`used`, `size`, `cost`). It therefore goes to
`session_update.context`. The backend used to read `usage_update` as token
usage. Every one of them recorded zero tokens and counted as a separate request.

## Permission requests

```json
{
  "request_id": "exec-fa4e…",
  "tool_call_id": "exec-fa4e…",
  "tool": { "name": "exec_command", "source": "lemma", "server": null, "title": "…", "kind": "execute" },
  "input": { "cmd": "echo from-mcp" },
  "options": [ { "option_id": "allow_once", "name": "Allow", "kind": "allow_once" } ]
}
```

Before emitting it, the host releases the gated call as a `tool_call` if it was
still being held, so the approval card follows the call it asks about.

The backend writes it as a `request_approval` call whose `agent_host_permission`
marker carries the request id, the options and the gated call's `input`. The
web card reads that marker: it shows the input as the call's arguments, offers
"approve for this conversation" only when an `allow_always` option exists (and
labels it with that option's name), and says when the request runs out. The
host denies an unanswered request itself after 30 minutes
(`PERMISSION_DECISION_TIMEOUT`), so a card past that point, or one whose run
has ended, reads "Expired — the agent continued without it" instead of
offering buttons.

A request that gates one of Lemma's own MCP tools never reaches Lemma. The host
answers it itself, because Lemma already authorizes those tools on every
call. Request ids and call ids are shortened the same way (see
`shorten_object_id`), so a long id cannot make the two stop matching.

## Steering

What a person types while a turn is running. ACP v1 has no method for adding
input to a `session/prompt` in flight, but both pinned adapters implement the
same extension for it, and the host uses it where it exists:

| Adapter | Advertises | `_session/steering` answers |
|---|---|---|
| Claude Code (`claude-agent-acp` 0.62) | `initialize` → `_meta.steering.supported: true` | `injected` (pushed onto the SDK's streaming input at priority `now`), or `startedNewTurn` when no turn was in flight |
| Codex (`codex-acp` 1.1) | the same | `injected` (Codex `turn/steer`), or `startedNewTurn` when the turn had just ended |
| OpenCode (native ACP, 1.18) | nothing | never sent |

The host publishes the advertisement as the harness capability `steering`, and
Lemma sends `STEER_RUN` (`message_id`, `prompt`) only to a harness that has it
-- which is also what keeps the command away from a host too old to parse it.
The run's driver sends each steer once its own prompt is out, and reports one
`steer_result` per message, ordered in the run's stream where it landed:

```json
{ "delivered": true, "detail": null }
```

Only `injected` is `delivered: true`. Everything else is `false` with a reason:
`unsupported` (the run's adapter did not advertise it after all), `turn_ended`
(the turn finished before the steer reached it, or the adapter answered
`startedNewTurn` -- a turn of the adapter's own that no Lemma run is reading, so
the host cancels it), or the adapter's error. An undelivered message is still
queued in Lemma, and the follow-up turn that starts when this one ends delivers
it; so does every message for a harness that cannot steer.

## Golden transcripts

`desktop/agent-host/tests/fixtures/acp/<adapter>@<version>/<prompt>.jsonl` are
real sessions with the pinned adapters, recorded by
`tests/fixtures/acp/record_transcript.py` against a stand-in Lemma MCP server
(`fake_lemma_mcp.py`). `tests/normalize_golden.rs` replays each one through the
adapter's normalizer and compares the result with the checked-in
`<prompt>.expected.json`. A test also fails when `agent-adapters.lock.json` pins
an adapter version that has no transcript directory, so bumping an adapter
means recording it again.

To record or re-record one (this uses a little real model quota, and the agent
has to be signed in):

```bash
cd desktop/agent-host/tests/fixtures/acp
python3 record_transcript.py --adapter codex --prompt tools \
  --out codex@1.1.7/tools.jsonl \
  --env CODEX_PATH="$(which codex)" -- /path/to/codex-acp
UPDATE_GOLDEN=1 cargo test -p lemma-agent-host --test normalize_golden
```

Review the diff of `*.expected.json` as you would review code. That diff shows
exactly what users will see change.
