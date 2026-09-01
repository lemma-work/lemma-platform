# Agent conversations

Status: the backend is built; the web workspace is not. What landed, and what
did not, is at the end under [Where this stands](#where-this-stands).

## The model

A conversation with an agent is **one persistent thing per agent**, not one per
session. You open Lemma, you are already in it, and the last thing you said is
above the composer. You can still create more, and you can still start a new
topic inside one — but neither is the default path, and neither is how talking
to an agent *begins*.

A conversation starts as a DM: one person, one agent. From there people and
agents can be added to it. That single sentence is what makes the rest of this
document necessary, because the current model has exactly one owner per
conversation and resolves every permission question through them.

Four rules follow from it:

- **A run acts as the sender**, not as the conversation. Whoever typed the
  message is the identity the agent's tools execute under.
- **With more than one agent present, an `@mention` addresses one of them.** An
  unaddressed message reaches a small router model that decides who — if anyone
  — answers. Silence is the expected answer.
- **Everyone sees the answer; only the sender sees the working.** A run's final
  text is the conversation's; its tool calls, tool results and reasoning belong
  to the person who triggered it.
- **A subthread is created deliberately**, by a person branching or an agent
  spawning. It is not one per turn.

The noun stays **conversation**. `channel` is already a Slack channel
(`external_channel_id`) and already a delivery route
(`agent_surfaces/services/notification_channels.py`); a third meaning is not
worth the shorter word. This document says "conversation" for the persistent
thing, and "channel" only where it means a Slack one.

## What already exists

The reason this is a proposal and not a rewrite: most of the storage and all of
the history policy already assume a long-lived conversation.

`agent_conversations` already has a nullable `agent_id` (NULL is the pod's
default assistant), a `parent_id`, a `conversation_type`, and `is_archived`. It
also already carries the index this design needs as a *uniqueness* key:

```
ix_agent_conv_user_pod_agent_roots
  (user_id, pod_id, COALESCE(agent_id, '00000000-…-0001'))
  WHERE parent_id IS NULL
```

That sentinel is `DEFAULT_POD_AGENT_ID` from `app/core/authorization/delegation.py`
— the pod assistant's id. The key for "the conversation between this person and
this agent" is already spelled out in the schema; it is simply not unique yet.

History policy already treats conversations as things that do not end.
`app/modules/agent/services/runtime_history.py` keeps the most recent five runs
in full, caps a conversation at sixty runs, and elides everything older to its
first and last message with a synthetic notice in between.
`app/modules/agent/services/context_budget.py` compacts at 80% of whatever
window the model actually has. The docstring on the former records that the web
UI once had *no* bound and a 400-turn conversation loaded every run it had ever
had. The permanent conversation is already partly the operating reality; what
changes is that it becomes the intent.

Addressing exists too, for external surfaces.
`app/modules/agent_surfaces/services/surface_routing.py` defines `_addressed()`
as `mentioned_agent or is_thread_reply`, and group surfaces already require it.
The second clause is the important one: a reply inside the agent's thread counts
as addressed, so a mention is *sticky* within a subthread. Mention once, then
talk normally. Native conversations should use the same predicate rather than
growing a second one.

Finally, the model already learns who spoke in a group. `_sender_label` in
`app/modules/agent/infrastructure/harnesses/pydantic_ai_history.py` reads
`sender_display_name` / `sender_email` / `sender_phone` off message metadata and
renders it into history. The prompt half of multi-sender is solved; it is
metadata-shaped and surface-only, which is what has to change.

## Membership and access

Today a conversation has one owner and the check is equality:

```python
# app/modules/agent/services/conversation_access.py
if conversation.user_id != user_id:
    raise ConversationNotFoundError()
```

There is no participants table anywhere in the module. Adding people starts
here, ahead of everything else in this document.

The shape:

| Concern | Decision |
| --- | --- |
| Members | A `conversation_participants` row per person and per agent, with a role. Pod membership is not sufficient — a conversation is narrower than a pod. |
| Access check | Membership lookup replaces the equality test. It keeps returning not-found rather than forbidden, for the reason already documented there. |
| `conversation.user_id` | Demoted to *who opened it*. It stops being an authorization input. |
| Agents present | Rows in the same table. This is what gives the router a roster and the mention parser a namespace. |

Demoting `conversation.user_id` is the wide part of the diff. It is read as the
acting identity in roughly twenty places outside tests — `conversation_turns`,
`snooze_wake_service`, `queued_followup`, `message_reply_service`,
`conversation_title_service`, `conversation_mcp_service`, the pydantic-ai
harness. Each one has to be re-pointed at an identity that now varies per run.

One layered check moves with it. `widget_controller` re-applies
`conversation.user_id == viewer` on top of `CONVERSATION_READ` specifically
because that permission is pod-wide and would otherwise let any pod member read
another member's widget HTML. Under participants that becomes a membership
test — and given the visibility rule below, probably a sender test.

## Who a run acts as

`RunIdentity` already carries a `user_id`
(`app/modules/agent/services/run_identity.py`) and is documented as everything
fixed for the whole run. That is the correct home for the acting identity. The
move is:

- `conversation.user_id` — who opened it. Provenance, not permission.
- `RunIdentity.user_id` — who this run acts as. Every authorization context,
  every tool grant, every RLS decision reads this.

This is what makes multi-person conversations tractable at all. Grants stay per
person; a conversation does not become a shared credential. If you cannot run a
function, an agent you talk to cannot run it for you, and the existing
`require_agent_actions` check on `agent.execute` keeps meaning what it means.

### Runs with no sender

Not every run has one. Snooze wakes, queued followups, title generation and
scheduled runs all build their context from `conversation.user_id` today
precisely because there is nobody typing. Under run-as-sender they need a
recorded actor, and it should be **captured at setup time, not resolved at fire
time** — "this schedule acts as the person who created it," written down when
the schedule is created. Resolving it later, from membership, means the identity
a background run holds can change when someone joins or leaves.

This is the same question already open on scheduled runs, where an unattended
run holds user-equivalent authority. This design does not answer it; it does
make the answer apply in one more place, and it makes the actor explicit enough
that the answer has somewhere to live.

## Visibility: the answer is shared, the working is not

Run-as-sender fixes what an agent may *do* on someone's behalf. It does nothing
about what the result then shows to everyone else standing in the room. A run
acting as A uses A's grants, and its output lands where B can read it.

The rule:

- **Final answer** — the run's assistant `TEXT` — belongs to the conversation.
  Everyone present sees it.
- **Working** — `TOOL_CALL`, `TOOL_RETURN`, `THINKING` — belongs to the sender.
  Only they see it.

`MessageKind` already draws exactly this line, so rendering needs no new
concept: filter by kind per viewer and stop.

### The unit is the run, not the message

Prompt construction cannot do the same thing, and this is the constraint that
shapes the feature.

`pydantic_ai_history` is explicit that its one subtle rule is tool pairing. When
a call has no matching return, `_build_tool_batch` does not drop it — it
**synthesizes a failure return**, deliberately, to preserve the
`tool_use`/`tool_result` pairing Anthropic requires. So stripping A's tool
returns out of B's history would tell the model that A's tools *failed*. That is
not a redaction; it is a false statement that changes what the agent does next.
Keeping the calls and dropping only the returns is no safer, because tool
arguments carry the sensitive query about as often as the result carries the
sensitive answer.

So a run's working is included whole or excluded whole. For B's turn, A's
earlier runs reduce to the question and the final answer.

That is the elision `runtime_history` already performs — trimming whole runs at
a time so a tool call is never separated from its return, and reducing an old
run to its first and last message with a notice in between. The same mechanism,
triggered by *who is looking* rather than by age. This is the reason the rule is
affordable.

Two things fall out without extra work:

- **Reasoning is already conditional.** `PendingThoughts` gates thought replay on
  the `protocol` argument, and a caller that does not name one replays no
  reasoning at all. The hardest part of "working" to redact is off by default.
- **Approvals land on the right person.** `interaction_sender_matches` already
  requires the resolver to be the same external user and fails closed when it
  cannot tell. If working is sender-private, only the sender sees the approve
  button — which is the correct answer and already the code's instinct.

There is a second, unrelated reason to want this: other people's tool payloads
were going to consume the context window anyway. The privacy rule and the
context budget push in the same direction.

### What it does not close

**Derived answers.** "Summarise the salary table" produces a final answer that
*is* the private data. Filtering by kind does nothing, and no plumbing will. The
control is the prompt: an agent should know who is present and be able to
decline to put something in a shared room. That is soft, and it should be
described as soft.

**Egress.** Once an answer leaves through a surface, that platform's membership
decides who reads it, not ours.

Both are reasons the visibility rule is a *default that prevents accidents*,
not an access-control system. The stated policy remains that a conversation is a
room: joining it is being trusted with what is said in it. Adding a member is a
grant, and the product should say so.

Resource rendering is not on this list. `display_resource` resolves content
through the pod's own services under the caller's context, so pod membership and
RLS already bound it.

## Addressing

Two stages, in order. The first is deterministic and the second is not, and that
ordering is the design.

**1. Explicit address.** An `@mention` of an agent present in the conversation,
or a message inside a subthread that agent is already answering. Reuse the
`_addressed()` predicate. An explicit mention must never reach a model to be
interpreted.

**2. The router.** Only for messages stage 1 did not claim. A small model gets
the last message, the roster, and a short window of recent turns, and returns
one agent or nobody.

Four constraints on it:

- **Ignore is the default and must be cheap.** Where people mostly talk to each
  other, most messages are not for any agent. A model asked "who should reply?"
  will find someone to be helpful with, so the prompt has to be biased hard
  toward silence and the call small enough that being wrong costs nothing.
- **Never route agent-authored messages.** Otherwise agent A posts, the router
  sees a new message, routes to agent B, who posts, and it talks to itself. A
  per-turn routing budget is the backstop; excluding agent authorship is the
  fix.
- **Skip it entirely when the answer is structural.** A DM with one agent — the
  default, and most of them — has one candidate and every message is addressed.
  No router call should ever be made there.
- **It decides *who*, never *what*.** The router does not summarise, rewrite or
  pre-answer. It picks a name or returns nothing, so a wrong decision costs one
  skipped reply rather than a fabricated one.

Choosing nobody is an ordinary outcome, and it means **no run is created at
all**: the message is stored, everyone watching is sent it, and
`AgentRunStartResult.agent_run_id` is null. The send endpoint answers that with
a single `unanswered` frame and closes, rather than holding a stream open
against a run that will never exist.

## Subthreads

A subthread is a deliberate branch. It is not one per agent run.

A run is an execution boundary: one message in, one pass of the harness. Keying
subthreads on run ids would produce a subthread per turn, which is not a thread.
Runs stay what they already are — the unit history is trimmed by, whole runs at
a time, so a tool call is never separated from its return.

Branch points belong on `AgentRun.parent_run_id`, which already exists.

They do **not** belong on `Conversation.parent_id`. That column already carries
two meanings — sub-agent spawn linkage and PROJECT pinning — and the codebase
explicitly pins the distinction between them (see the comment in
`app/modules/agent/tools/toolset_selection.py` and the sub-agent tests). A third
meaning on the same column is a bug nobody will be able to name.

A "new topic" control inside a conversation was built and removed. It reset the
context without ending the conversation, which sounded like the answer to the
objection above -- and was worse than the thing it replaced. Creating a new
conversation already gives a clean slate, says so unambiguously, and leaves the
old one where you can find it; a hidden mode that silently drops context is a
way to lose work by mis-clicking. Surfaces keep their own
``dm_conversation_reset_after_hours``, which is a different question: nobody
chooses it, so nothing is mis-clicked.

## What stays unsolved

**Compaction summaries become durable memory.** Today a compaction summary is a
transient prompt artifact on a conversation that will probably be abandoned. In
one that never ends, the summary *is* its memory of its own past. It should be a
durable, inspectable record — something a person can read and correct — rather
than a string regenerated inside a prompt. This is the largest piece of
engineering in the design and it is not schema work.

## Order of work

1. **Participants table and the access check.** Replaces the equality test in
   `conversation_access`. Useful on its own: it is what lets a second person see
   a conversation at all.
2. **Sender on messages.** A real column, not metadata. `_sender_label` already
   knows how to render it into history; it just reads the wrong place.
3. **Actor moves to the run.** `conversation.user_id` demotes to provenance;
   `RunIdentity.user_id` becomes the acting identity. Wide diff, and background
   paths need explicit actors.
4. **Visibility.** Kind filtering in rendering, run-granular elision in history.
   Ships with (1) — a second member without it is a leak.
5. **Conversation resolution.** Make the existing root index unique and resolve
   `(user_id, pod_id, agent_id)` instead of creating a new row. Existing
   conversations are not merged: the most recently active one per key becomes
   the persistent one, the rest stay as history.
6. **Subthreads and "new topic."** Then retire
   `dm_conversation_reset_after_hours` for native conversations.
7. **The router**, behind a flag, off by default, never reached in a one-agent
   DM.

The first two are worth having even if the rest slips.

## Open questions

- Is the persistent conversation keyed `(user, pod, agent)` or `(pod, agent)`?
  This document assumes the former — a DM that grows — because it is the only
  one that starts from an existing index and does not change what a conversation
  is on day one. The latter exists before anyone speaks, and it is a different
  product.
- Does a Slack channel where the agent lives *become* the Lemma conversation, or
  sit beside it as a linked surface? Today `surface_conversation_links` creates
  one per external thread. Under this design that is either a subthread or a
  duplicate, and duplicate is the wrong answer.
- What does removing a member do to history they have already read?

## Where this stands

Built. The tables below are the map from a rule above to the code that holds it.

**Backend**

| Piece | Where |
| --- | --- |
| Membership | `agent_conversation_participants` (migration 0025, backfilling an OWNER row per conversation), `domain/participants.py`, `infrastructure/conversation_participant_store.py` |
| Access | `conversation_access.validate_conversation_access` — the opener, or anyone added; and an agent present in the conversation may be addressed in it |
| Sender | `agent_messages.sender_user_id` (migration 0026), written on every user message |
| Actor | `agent_runs.triggered_by_user_id` (migration 0027, backfilled from the conversation owner). The runner reads it off the run rather than the job payload, and it is the identity the tools, the usage reservation and the telemetry all use |
| Visibility | Kind filtering in `ConversationViewerQueriesMixin.list_messages`; run-granular collapsing in `runtime_history.select_runtime_history` |
| Resolution | `find_persistent_conversation` + `ConversationService.open_conversation`, behind `POST /pods/{pod_id}/conversations/open` |
| Subthreads | `runtime_history.apply_branch_lineage`, reached by `branch_from_run_id` on a send; the branch point is `AgentRun.parent_run_id` |
| Router | `services/agent_router.py` holds the rules, `agent_router_model.py` the model call; `TurnCoordinator._route_unaddressed` calls it on every send that named nobody. No flag: routing is how a room with several agents works |
| API | `conversation_open_controller` — open, and list/add/remove participants |

**Web workspace**

| Piece | Where |
| --- | --- |
| Sender on a turn | `lib/assistant/turns.ts` reads `messageSenderUserId` into `ChatTurn.senderUserId` |
| Trace suppression | Same file: another person's trace is dropped whole and the turn is marked `traceWithheld`, which renders as "Worked privately" |
| Byline | `assistant-turn.tsx`, from the roster; never shown for your own messages |
| Participants panel | `components/conversations/conversation-participants.tsx`, in the assistant header |
| Sidebar grouping | `assistant-experience-sidebar.tsx` groups into Ongoing (newest per agent, the same rule the server resolves with) and Earlier |
| `@agent` | Agent participants join the composer's mention typeahead; `lib/assistant/addressed-agent.ts` reads the mention back out of the text and the send carries it as `agent_name` |

Naming an agent in a send routes that turn to it, which is why the access check
accepts an agent that is present rather than only the conversation's own.

**Not built**

- **Nothing writes `parent_run_id` from the UI yet.** The API takes
  `branch_from_run_id` and the history follows the branch; the transcript has no
  "branch from here" affordance, so subthreads are reachable through the API
  only.
- **The router has never run against a real model.** The rules, the guards and
  the wiring are tested; the model call itself has only ever been exercised
  against doubles.
- **`dm_conversation_reset_after_hours` is untouched**, and stays that way. It
  answers a question native conversations do not have.

Uniqueness is not enforced on the resolution key. Every account already has
many conversations per `(user, pod, agent)`, so a unique index cannot be added
without merging or discarding history. `find_persistent_conversation` takes the
newest live one instead; two simultaneous first messages could leave a spare,
which the next resolve does not pick.
