# Agent module

## Purpose

`app/modules/agent` owns agent definitions, conversations/messages, model runs,
runtime profiles, Agent Host dispatch, tool assembly, approvals, realtime
streaming, MCP access, widgets, and usage handoff. It is the central execution
module; external delivery belongs to [agent surfaces](agent_surfaces.md), and
sandbox lifecycle belongs to [workspace](workspace.md).

## Runtime contributions

| Contribution | Behavior |
| --- | --- |
| API routers | Agent CRUD/permissions, conversations/messages/SSE, runtime profiles, Agent Host pairing/dispatch, tools, widgets |
| Redis consumer | Converts agent lifecycle events into queued work and title generation |
| streaq tasks | Run agents, generate titles, reconcile orphaned runs |
| Published stream | `agent_events` |
| Mounted MCP apps | Conversation and pod tool servers are assembled by the backend root using agent services |

Durable lifecycle events are staged in the PostgreSQL outbox and reach Redis
Streams only through the core message bus. Transient token/status frames use the
core realtime-channel port with a Redis Pub/Sub adapter; each SSE connection
leases one subscription connection and releases it on completion, failure, or
cancellation.

## Main data model

| Table | Meaning |
| --- | --- |
| `agents` | Named prompt, schemas, toolsets, runtime selection, visibility |
| `agent_runtime_profiles` | Organization/user/system model provider configuration and encrypted credentials |
| `agent_hosts`, `agent_host_harnesses` | Paired Agent Host installations and their harness snapshots |
| `agent_host_commands`, `agent_host_run_leases` | Durable command handout and the single dispatch fence per run |
| `agent_conversations` | Pod thread, the agent it belongs to (the pod's assistant is a row like any other), parent/subagent and workspace metadata |
| `agent_messages` | User/assistant/tool messages and structured parts |
| `agent_runs` | One execution attempt, status, usage, errors, stop state, harness metadata |
| `agent_approval_decisions`, `agent_feedback` | Durable interaction/audit records |

## API groups

| Routes | What they do |
| --- | --- |
| `/pods/{pod_id}/agents` | Agent CRUD plus resource permission replacement |
| `/pods/{pod_id}/conversations` | Create/list/read/update, messages, approvals, send, stream, stop |
| `/organizations/{org}/agent-runtime/profiles` | Discover/create model runtime profiles |
| `/me/runtime/agent-hosts...`, `/agent-host/*` | Agent Host pairing, harness catalog, command poll, and event append |
| `/tools/*` | Server-side web search and feedback endpoints used by runtimes |
| `/widgets/serve...`, `/pods/{pod}/widgets...` | Render/submit a tool widget and mint an authenticated embed URL |

## Run lifecycle

```mermaid
stateDiagram-v2
    [*] --> QUEUED: user message committed
    QUEUED --> RUNNING: process_agent_run claims run
    RUNNING --> WAITING_FOR_INPUT: ask-user or approval tool
    WAITING_FOR_INPUT --> QUEUED: answer/decision resumes conversation
    RUNNING --> COMPLETED: final output persisted
    RUNNING --> FAILED: model/tool/runtime error
    QUEUED --> STOPPED: stop before execution
    RUNNING --> STOPPED: cooperative stop
```

The runner resolves a runtime profile and harness (`pydantic_ai`, Agent Host,
or test harness), builds only the allowed toolsets, creates short UoWs for
message/status transitions, performs model/tool I/O outside them, publishes
realtime frames, and records usage. Tool calls receive a delegated workload
context; destructive operations require a standing grant or session approval.
Agent Host runs are fenced by a per-run lease row, so a host that reconnects
cannot double-dispatch a run already in flight.

Subagents are child conversations with inherited workspace context and reduced
toolsets. Widgets are tool outputs stored in conversation context and served
through signed, purpose-bound embed access.

## Key dependencies

- Pod/identity: tenant, membership, authorization, delegation.
- Datastore/connectors/function: agent-callable resources.
- Workspace: shell, Python, browser, file, and long-lived process sessions.
- Usage: reserve and record model cost.
- Agent surfaces: surface context and platform tools; this dependency is
  currently bidirectional.

## Tests and operations

The test suite covers tool assembly, messages, approvals, cancellation,
runtime profiles, Agent Host dispatch, MCP, widgets, usage, subagents, and
mocked/real harness paths. This module carries the largest orchestration classes
in the backend; their size and cross-module coupling are held flat by the
`make architecture` ratchet rather than reduced.
