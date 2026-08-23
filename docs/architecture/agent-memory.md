# Agent memory

How an agent keeps what it learns, and how that reaches its next conversation.

## Memory is pod files

There is no memory store. An agent's durable facts are ordinary files in the
pod's datastore, under two roots:

| Root | Who can read it |
|---|---|
| `/memory` | Everyone in the pod — shared knowledge |
| `/me` | One user, and only that user — the personal tree that already existed |

That choice is load-bearing rather than incidental. Files already have
per-user isolation, path-prefix authorization, versioning, and full-text and
semantic search; a purpose-built memory table would have had to reinvent each
one. It also means memory is browsable, editable, and deletable through every
interface the pod already has, instead of only through the agent that wrote it.

Each root has a per-agent subfolder, named by the agent's slug:

```
/memory/AGENTS.md                    pod-wide index, every agent
/memory/agents/<slug>/AGENTS.md      this agent, shared pod-wide
/me/AGENTS.md                        this user, private
/me/agents/<slug>/AGENTS.md          this agent about this user, private
```

Lem, the pod-default assistant, needs no special case: its synthetic agent
entity is named `pod_default`, so the same rule gives it
`/memory/agents/pod-default/`. Those paths are computed in exactly one place —
[`agent_memory_paths.py`](../../lemma-backend/app/modules/agent/domain/agent_memory_paths.py)
— because two callers must agree on them exactly: whatever reads them into the
prompt, and whatever tells the agent where its writes should land. If those
drifted, an agent's notes would silently stop showing up in its own briefing.

## AGENTS.md is the part that is always loaded

Everything else in memory is found by searching or by following a pointer.
`AGENTS.md` is different: all four are read into the Runtime Context brief on
every run, before the agent does anything, so recalling a fact never depends on
the agent remembering to go looking for one.

That is also why `AGENTS.md` is an **index**, not a store. It should hold one
line per topic and a pointer to the file with the detail. Anything written into
it is paid for on every turn of every conversation, forever.

The platform does not rely on that being obeyed:

- Each index is truncated to `AGENT_MEMORY_INDEX_MAX_CHARS` at a line boundary,
  with a marker naming the file and how many lines were dropped — a silent cut
  would leave the agent unable to notice, and it is the only party that can fix
  a bloated index.
- The whole section is capped at `AGENT_MEMORY_SECTION_MAX_CHARS`, spent
  narrowest-scope first: this agent's private notes on this user, then that
  user's, then the agent's shared notes, then the pod's. The pod-shared index is
  the one every agent writes to, so it is the one most likely to grow and the
  first to lose room.
- Writing an oversized index through `pod_write_file` returns a warning saying
  how much of it will actually reach the prompt.

Both caps are per-module settings on the agent module — see
[configuration](../configuration.md).

## MEMORY is a capability

`AgentToolset.MEMORY` is what turns this on. It is unusual in carrying **no
tools**: memory is files, and reading and writing files already belongs to
`WORKSPACE_CLI` (the sandbox shell, via `lemma files`) and `POD` (the
`pod_read_file` / `pod_write_file` tools). What MEMORY contributes is the
contract in the prompt and the four `AGENTS.md` scopes in the brief.

It follows that MEMORY alone is inert — an agent told to write durable facts,
with nothing to write them with. `memory_is_active` requires MEMORY *and* one of
those two toolsets, and it gates all three places memory shows up: the prompt
fragment on the remote-harness path, the `MemoryCapability` on the in-process
path, and the brief's `## Your Memory` section. The agent editor refuses the
combination for the same reason.

The pod-default assistant has MEMORY in its fixed toolset. User-created agents
get it only when granted, like any other capability.

Granting MEMORY also provisions `/memory` and derives a `folder.write` grant on
it, because a capability that says "remember this" without the permission to
write is a switch that does nothing. That grant is recomputed from the toolsets
on every save rather than stored once: grant writes have replace semantics, so a
grant applied once would survive exactly until the next edit.

## Staleness

The runtime brief is cached, and its two halves are cached apart, because they
go stale for different reasons.

The **inventory** half — tables, agents, functions, files — changes when somebody
edits the pod, and a short TTL is the whole answer.

The **memory** half changes when the agent writes a fact mid-conversation. A TTL
alone would mean an agent that cannot recall what it just learned, so this half
is invalidated on write. Its cache key is ordered `{pod}:{user}:{agent}` so the
two invalidations are prefix deletes: a write under `/memory` drops the whole
pod's entries, a write under a personal tree drops that one user's across every
agent.

Two things trigger it, because no single one sees every writer:

1. `pod_write_file` invalidates inline — reliable, in-process, and the path for
   agents using the pod tools.
2. A subscriber on the datastore event stream covers `lemma files write` from
   the workspace shell, which reaches the datastore over HTTP in the API process
   and never enters the worker running the agent.

A missed invalidation is not a correctness failure — the entry still expires on
its TTL, which is the behaviour that existed before any invalidation at all.

## What this does not do

Memory is not consolidated, deduplicated, or reconciled. Two agents can write
contradicting facts to `/memory` and nothing will notice. There is no dedicated
UI for reviewing or deleting what an agent knows beyond the ordinary file
browser, and memory does not travel in a pod bundle export.
