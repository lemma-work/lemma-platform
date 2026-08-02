# Agents can sleep

**Status:** Implemented (backend + skills) · **Surface area:** `lemma-backend`,
`lemma-skills`, regenerated `lemma-python` SDK. The conversation-list state in
`lemma-frontend` is **not** built — a snoozed conversation still reads as
"Waiting", which is the one place the product currently lies about what is
happening.

## The change in one sentence

An agent can suspend its own turn for a while and wake where it left off —
reusing, almost entirely, the pause the product already has for humans.

## Why

An agent has exactly two ways to stop mid-turn today: finish, or block on a
person. Anything that needs to happen *later* has to leave the agent — become a
workflow with a [wait node](../../lemma-backend/app/modules/workflow/domain/nodes/wait_until.py),
or a standing trigger. Both throw the turn away. The agent that knew why it was
waiting is gone, and what comes back is a fresh run holding a payload and no
memory of the intent behind it.

That is the wrong shape for the most ordinary agent task there is: *do a thing,
wait for the world to change, finish the thing.* Chasing an approval. Watching
an invoice. Giving a build eight minutes and checking back. Each one is one
agent turn with a gap in the middle, and today each one has to be modelled as
two disconnected executions with a data structure passed between them.

## This is smaller than it looks

Agent conversations already suspend and resume durably. `ask_user` and
`request_approval` do it on every call:

| Step | Where |
| --- | --- |
| A tool raises `AgentInputRequired(tool_call_id, kind)` — control flow, not an error | [tool_errors.py:45](../../lemma-backend/app/modules/agent/tools/tool_errors.py) |
| The harness catches it and emits `WAITING` instead of failing the run | [pydantic_ai.py:173](../../lemma-backend/app/modules/agent/infrastructure/harnesses/pydantic_ai.py) |
| The runner finishes the run `COMPLETED`, flips the conversation to `WAITING` | [agent_runner_service.py:686](../../lemma-backend/app/modules/agent/services/agent_runner_service.py) |
| On resolution: synthesize the tool return, append it, start a fresh run that replays history | [conversation_service.py:626](../../lemma-backend/app/modules/agent/services/conversation_service.py) |

The turn *is* serialized — as message history — and replay is the resume. The
agent wakes believing its tool call just returned.

So a snooze is that pause with a non-human wake source. What is genuinely new is
narrow: a durable row saying what we are waiting for, two wake sources, and
prying step four loose from the approval semantics it is currently welded to.

The record bus exists too. Datastore emits `DatastoreRecordEvent` →
[datastore_consumer.py](../../lemma-backend/app/modules/schedule/handlers/datastore_consumer.py)
→ [DatastoreEventHandler](../../lemma-backend/app/modules/schedule/services/datastore_event_handler.py)
→ matching `DATASTORE` schedules → the agent or workflow. It matches on
`(pod_id, table_name, operation)` and nothing finer.

## Decisions

**Agent-initiated only.** No "snooze this conversation until Monday" from the
UI. That needs a cancel/wake API and an interrupt into a mid-flight run, and it
is a different feature wearing the same word.

**Time only — no record waits.** Scoped in, designed, built, then cut before
shipping. Reacting to a row changing is what a
[trigger](./schedules-into-triggers.md) already does, and a second path to the
same datastore event is a duplication this codebase can do without. Cutting it
also removed the two hardest pieces — a second consumer group on a hot event
stream, and a pre-suspend race check — for a wake source whose only real
advantage over a timer was avoiding a poll loop. Keeping *one* way to react to
data is worth more than that.

The removal was deliberately cheap to make: `AgentWaitType` still exists with a
single member, so a future wake source arrives as a new member rather than a new
table. See *Not in this change*.

**24 hours, hard cap.** Two independent reasons, and the second is the one that
bites:

1. Every wake replays full conversation history. A day is roughly where that
   stays honest.
2. `reply_window_hours` in [platform_capabilities.py](../../lemma-backend/app/modules/agent_surfaces/platforms/platform_capabilities.py)
   is real: WhatsApp's 24h customer-service rule means an agent that sleeps
   longer than the window **cannot deliver its own result** on the surface it
   was asked on. A snooze that outlives its ability to reply is not a feature.

## Naming

The domain noun is **wait**, mirroring
[`WorkflowRunWaitEntity`](../../lemma-backend/app/modules/workflow/domain/wait.py)
exactly. The tool verb is **`snooze`**. Those are two words because the workflow
side already splits them the same way — a `wait_until` node returns a
`WaitRequest` — and the symmetry is worth more than collapsing the pair. The
conversation state reads **"Snoozed until 4:30pm"**, never "Waiting", which is
already taken by *waiting for you*.

## Design

### The wait row

```
agent_conversation_waits
  conversation_id, agent_run_id, tool_call_id, pod_id
  wait_type      TIME          -- one member today; see Decisions
  status         ACTIVE | COMPLETED | CANCELLED
  external_ref   the scheduler timer id
  scheduled_at
  spec           the snooze request, verbatim
  created_at, completed_at
```

Shape-identical to `WorkflowRunWaitEntity` on purpose, so the reconciliation
sweep is a copy rather than an invention.

**Not** ephemeral rows in `schedules`. A schedule is a standing rule someone
configured; a wait is a suspended execution. Per-conversation schedule rows
would inherit `is_active`, the consecutive-failure counter, and the
circuit-breaker deactivation in [`_apply_failure_policy`](../../lemma-backend/app/modules/workflow/services/schedule_start_service.py)
— every one of them meaningless for a one-shot wait, and one of them
(the breaker) actively harmful.

Human pauses stay on the approval-decision row for now. The end state is that
the wait row records *that it is paused* and the decision row records *what the
person said*; that migration is not worth doing before this ships.

### The tool

```python
class SnoozeRequest(BaseModel):
    reason: str            # user-visible: "waiting for the nightly build"
    seconds: int           # clamped to [30, 86_400]
    note_to_self: str | None

class SnoozeResponse(BaseModel):
    woke_because: Literal["TIMER", "CANCELLED"]
    slept_seconds: int
    note_to_self: str | None
```

`woke_because` is deliberately narrow, and the docstring leans hard on what it
does *not* say: `TIMER` means the time elapsed and nothing more. The failure
mode this exists to prevent is an agent treating "I woke up" as "the thing I was
waiting for happened."

Below 30s the call is **rejected rather than clamped**. A clamp would be
friendlier, but an agent asking to sleep five seconds has mistaken this for
`sleep()`, and silently stretching the number teaches it nothing. The ceiling
*is* clamped, because 24h is a policy limit rather than a misunderstanding.

A new opt-in `AgentToolset.SNOOZE`, deliberately **not** in
`POD_DEFAULT_AGENT_TOOLSETS` — user-created agents get exactly what they were
built with, and this one has real cost and abuse surface.

### Choosing the duration is the agent's hardest decision

Borrowed from Claude Code's `ScheduleWakeup`, which solves this exact problem
and states it well: **pick the delay from what you are actually waiting for, not
out of habit.** A job that takes eight minutes deserves one ~500s sleep, not
eight 60s checks. The same doc's sharper rule — don't schedule short wakeups to
poll work that will notify you when it finishes — lands here as *don't snooze to
poll something you could just check*.

This matters more in Lemma than it does there, because our wake replays the
entire conversation. A poll loop isn't merely wasteful, it is quadratic in cost.
So the guidance lives in three places the model actually reads: the tool
docstring, the `seconds` field description, and
[prompts/snooze.md](../../lemma-backend/app/modules/agent/prompts/snooze.md).

`reason` follows the same source: it is written *for the user*, shown while the
agent sleeps, and specific — "waiting for the nightly build" beats "waiting" —
so nobody has to guess the agent's cadence.

### The wake

Reuse the wait-until plumbing unchanged: `schedule_once_job` with
`{conversation_id, wait_ref, source: "agent_snooze"}`, then a third branch in
[`handle_schedule_fired`](../../lemma-backend/app/modules/workflow/services/schedule_start_service.py)
beside the existing `workflow_run_id` one. APScheduler's `SQLAlchemyJobStore`
already survives restarts. This is the proven path; the risk here is near zero.

A cron sweep fires any ACTIVE wait already past `scheduled_at`, self-healing a
lost scheduler event — modelled directly on
[`reconcile_stale_waits`](../../lemma-backend/app/modules/workflow/services/run_resume_service.py).
Racing the primary timer is harmless: every wake claims the row under a lock, so
the second one is a no-op.

### The shared resume primitive

Pull the reusable half out of `_reconcile_approval_resume`:

```
resume_conversation_from_pause(conversation, tool_call_id, tool_name, tool_result)
  1. lock_conversation                       # exists; serializes concurrent wakes
  2. tool_return already exists → no-op      # exists; idempotency
  3. append MessageDraft.of_tool_return(...)
  4. commit + publish_conversation_event
  5. start a fresh run iff no active run and no unresolved sibling pause
```

Both the approval path and the snooze wake call it. This is the highest-value
piece in the whole change: it converts "pause on a human" from an
approval-specific accident into a first-class capability, and it is a pure
refactor covered by existing tests.

## What has to change with it

| Constraint | Consequence | Disposition |
| --- | --- | --- |
| `supports_pause_signal` is `harness_kind == LEMMA` ([agent_runner_service.py:264](../../lemma-backend/app/modules/agent/services/agent_runner_service.py)) | Codex / Claude Code / OpenCode run tools over MCP and own their session — they cannot pause mid-call | **Out of scope.** Return the same `interaction_fallback` shape `ask_user` uses |
| `workflow_wait_max_age_seconds` is 6h and [`_expire_overdue_wait`](../../lemma-backend/app/modules/workflow/services/run_resume_service.py) fails any AGENT wait past it, exempting only TIME | An agent node whose agent snoozes 8h **fails the workflow** while the agent is healthy | **Required companion fix.** Give the AGENT wait a `scheduled_at` and expire it like a TIME wait |
| `get_conversation_status` returns `WAITING` for both a human block and a snooze ([workflow_agent.py:155](../../lemma-backend/app/composition/workflow_agent.py)) | A workflow cannot tell "blocked forever" from "wakes in an hour" | **Required.** Add a wait reason to the status |
| Workspace sessions are idle-reaped ([workspace_activity_store.py:148](../../lemma-backend/app/modules/workspace/services/workspace_activity_store.py)) | The sandbox is gone on wake; `/workspace` state does not survive | **Docstring, in plain words.** Re-establish `cwd` on wake. This will be the top source of confused behavior |
| `ask_user` fails fast on email surfaces because pausing strands the run | Snooze self-resolves, so it is legitimate there — the guard must not be copied | **Do not copy** `platform_is_email`. Surface renders "snoozed", not "waiting for you" |
| A snoozing sub-agent blocks its parent's tool call while the parent is mid-run | Parent hits its own limits waiting on a child that is deliberately asleep | **Disallow in sub-agent conversations.** `RunToolAssembler` already drops toolsets for sub-agents |
| Each wake replays full history | A snooze loop is a token bonfire | Min-duration floor, max snoozes per conversation, wake counted in usage |
| A timer event gets lost | Zombie wait | Sweep modelled on [`reconcile_stale_waits`](../../lemma-backend/app/modules/workflow/services/run_resume_service.py): fire any ACTIVE wait past `scheduled_at` |

## Phasing

| Phase | Scope | Risk |
| --- | --- | --- |
| 0 | Extract `resume_conversation_from_pause`; migrate the approval path onto it | Low — refactor under existing tests |
| 1 | `agent_conversation_waits` (migration `0011`, revises `0010_proactive_messaging`) · `snooze(seconds=…)` · timer wake · sweep · **the 6h ceiling fix** | Medium — the ceiling fix touches workflow outcomes |
| 2 | Conversation-list state, remote harnesses | — |

Phase 1 is small precisely because Phase 0 does the structural work. The ceiling
fix is the only part of it that is not mechanical.

## Where this gets taught

The tool docstring **is** the prompt — pydantic-ai sends it to the model as the
tool description, which is why `ask_user`'s docstring is eighteen lines of
behavioral guidance rather than a summary. Most of the teaching work for snooze
happens there, and it is where the sandbox-does-not-survive warning has to live,
in plain words, because no other surface reaches the model at the moment it
matters.

| Surface | Change | Gate |
| --- | --- | --- |
| [`snooze` docstring](../../lemma-backend/app/modules/agent/tools/) | Mechanics, the deadline discipline, `woke_because`, and "your sandbox will be gone" | — |
| [`agent_tool_schemas.json`](../../lemma-backend/agent_tool_schemas.json) | Regenerate: `uv run python scripts/export_agent_tool_schemas.py` | **CI** — `test_checked_in_agent_tool_schemas_match_live_tools` fails on drift |
| [`event_catalog.py`](../../lemma-backend/app/core/log/event_catalog.py) | Register every new snooze/wake event with its level and fields | **CI** — `test_logging_contract_static.py` |
| [`prompts/snooze.md`](../../lemma-backend/app/modules/agent/prompts/) | Per-toolset fragment, following `todo.md` / `speech.md` | — |
| `prompts/agent_base.md` | **No change.** Seven lines of rules that hold across every pod; snooze is opt-in | — |

### Adding a toolset touches four places

| Place | Drift risk |
| --- | --- |
| [`value_objects.py`](../../lemma-backend/app/modules/agent/domain/value_objects.py) — the enum | source of truth |
| [`cli_app/enums.py`](../../lemma-cli/lemma_cli/cli_app/enums.py) — `TOOLSETS` | **none.** Derived: `tuple(v.value for v in AgentToolset)` off the generated SDK. But the SDK must be regenerated, or everything downstream of it is silently a release behind |
| [`agents.md`](../../lemma-skills/lemma-builder/references/agents.md) — the toolset table | **was** unguarded; now covered by `test_builder_skill_documents_every_grantable_toolset` |
| [`agent-access-dialog.tsx`](../../lemma-frontend/components/agents/agent-access-dialog.tsx) — user-facing labels | **unguarded.** Hand-written on purpose (`WORKSPACE_CLI` told nobody anything). Miss it and nothing fails — the toolset simply never appears in the access dialog, so nobody can turn it on |

Skill sources live at repo-root [`lemma-skills/`](../../lemma-skills/);
`lemma-cli/lemma_cli/skills/` is a gitignored build copy. Edit the former.

### The skills are where this actually breaks

Because this is one repo, the skill edits are not a follow-up — they land in the
same commit as the tool, and there is no window where a shipped skill describes
a backend that does not have `snooze`, or vice versa. That is worth spending:
the existing gate's docstring is *"make model-facing tool documentation
freshness-enforced in the test suite,"* and a skill table listing agent toolsets
is model-facing tool documentation by any reading. Assert the toolset table in
`agents.md` covers every `AgentToolset` member and the drift becomes a CI
failure instead of something a reviewer has to notice.

[`lemma-builder/references/agent-tools.md`](../../lemma-skills/lemma-builder/references/agent-tools.md)
has a section headed *The pause / resume model* whose first sentence is
"`ask_user` and `request_approval` are **pausing** tools." That sentence becomes
false the day this ships, and it is load-bearing — it is how a builder agent
learns what pausing means. The surface matrix above it needs a snooze row too:
what does a snoozed conversation look like on Slack, Telegram, WhatsApp, email?

Then:

- [`agents.md`](../../lemma-skills/lemma-builder/references/agents.md)
  — the toolset table gains `SNOOZE`, noted as opt-in.
- [`schedules-and-triggers.md`](../../lemma-skills/lemma-builder/references/schedules-and-triggers.md)
  — **the boundary rule.** A trigger *starts* work nobody was doing; a snooze
  *resumes* work already underway. Dropping record waits made this much less
  load-bearing than it was when both could react to a row changing — there is
  now exactly one way to do that, and it is a trigger.
- [`lemma-user/SKILL.md`](../../lemma-skills/lemma-user/SKILL.md)
  — an operator listing conversations has to tell a snoozed one from one blocked
  on them, or every sleeping agent reads as a stuck agent.

## Not in this change

**Waking to a proactive message.** An agent that wakes at 3am and wants to tell
someone is a *reach* — [`member_reaches` and the in-app inbox](../../lemma-backend/migrations/versions/2026-08-01_proactive_messaging_0010.py)
already model that, including the reply-window problem. Snooze should compose
with it, not reimplement it. Worth confirming the composition works before
Phase 1 ships, because "the agent woke up and could not tell anyone" is the
failure mode that makes the feature look broken.

**Record waits, of any granularity.** Reacting to a row changing is a trigger.
If a future case genuinely needs a conversation to resume on a data change
rather than a clock, it arrives as a second `AgentWaitType` member — the table,
the wake service, the sweep, and the resume primitive all already accommodate
one without modification. What it would need to bring with it is the part that
was cut: a matcher on the datastore stream, and a pre-suspend check to close the
race between deciding to wait and committing the row.

**Cancelling a snooze from the UI.** Needs the interrupt path described under
Decisions. `AgentWaitStatus.CANCELLED` and the `CANCELLED` wake reason exist and
are reachable (a wake against a deleted conversation takes that path), so the
contract is already in place for it.
