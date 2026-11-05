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

## When memory is written

It depends on whether somebody is waiting for the run.

**A run a person is waiting on answers first.** That is a web chat turn or a
message that arrived on a surface, run on Lemma's own harness. The agent writes
memory itself only when the person asks it to remember something, forget it, or
correct what memory says, and then does so in the turn. Anything else worth
keeping it leaves to the memory pass. Saving it first meant listing, reading and
writing memory files before answering: extra model calls the person sat through
for a fact they had only mentioned. The two contracts differ in one section,
`When to write` ([`memory_after_turn.md`](../../lemma-backend/app/modules/agent/prompts/memory_after_turn.md)
replaces it in [`memory.md`](../../lemma-backend/app/modules/agent/prompts/memory.md)),
so what is worth keeping and how it is written cannot drift between them.
[`memory_kept_after_turn`](../../lemma-backend/app/modules/agent/domain/agent_memory_paths.py)
decides which runs these are, once, when the run's context is built.

**The memory pass** runs once such a run completes. `stage_run_followups`
writes an `agent.memory` work item in the transaction that finished the run.
The pass ([`memory_pass.py`](../../lemma-backend/app/modules/agent/application/background/memory_pass.py))
reads that turn: what the person said, what the agent did and answered, and the
reply before it. Some of what a turn answers is stored on other runs: a manual
retry's original message stays on the run that failed, and the person's answer
to `ask_user` is stored beside the question on the run that paused. The pass
reads those too.

It reads in two steps, because most turns hold nothing to keep. First a screen
([`memory_screen.md`](../../lemma-backend/app/modules/agent/prompts/memory_screen.md)):
one request with the turn and no memory and no tools, which lists what in it
may be worth keeping or says `nothing` -- and most passes end there. Only a list
reaches the keeper, which gets the four indexes and that list (not the turn
again), and keeps what is new by the same contract, without its `When to
write` section
([`memory_pass.md`](../../lemma-backend/app/modules/agent/prompts/memory_pass.md)
frames it, and asks it to batch its reads and its writes). In doing so it:

- acts as the agent did, for the person the run worked for. That is not always
  the conversation's owner: in a shared conversation it is whoever spoke. It
  uses the agent's own pod file tools under the agent's own authorization, so a
  write invalidates the brief exactly as the agent's would.
- reaches only memory. It reads `/memory` and `/me`, and writes the two shared
  indexes, the agent's own two folders, and pod-shared topic files outside
  other agents' folders.
- runs on the run's own model and provider, so the turn goes nowhere it had not
  already gone, and asks it for no reasoning. Its usage is recorded under its
  own source, `agent_memory`.
- runs one at a time per pod, because every agent shares `/memory/AGENTS.md`,
  and in the automation work class, so it never queues ahead of a person's turn.
- is best effort. A failed attempt is retried once; a refusal that would only
  repeat, such as a usage limit or a model no longer configured, is not. A
  keeper that reaches its step limit keeps what it already wrote, and one that
  wrote nothing is retried rather than given up on.

The consequence is timing. A fact mentioned in passing lands in memory a few
seconds *after* the answer, not before it. A follow-up in the same conversation
still has the fact in the conversation itself, but a turn that starts before
the pass finishes, such as a new conversation opened straight away, may not
see it yet. An explicit "remember …" is still written in the turn, so it is
there before the answer.

**Every other run saves in the turn**, as all runs used to:

- schedules, workflows, triggers and sub-agents, where nobody waits and the run
  knows its own work best;
- Agent Host runs, where the pass would need a model Lemma does not run;
- runs answering contacts or other pods, whose words are never written to
  memory with a member's authority.

Setting `AGENT_MEMORY_AFTER_TURN=false` turns the pass off. Every run then
saves in the turn again, and no pass is queued.

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

Memory is not consolidated, deduplicated, or reconciled. The memory pass reads
one turn and adds to what is there; it never goes back over earlier turns or
over what other agents wrote. Two agents can write contradicting facts to
`/memory` and nothing will notice. There is no dedicated
UI for reviewing or deleting what an agent knows beyond the ordinary file
browser, and memory does not travel in a pod bundle export.
