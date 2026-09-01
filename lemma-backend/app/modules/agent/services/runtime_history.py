"""What history reaches the model, and how much of a run survives the trip.

Two policies live here. Surface conversations (Slack, Telegram, WhatsApp, ...)
bound prior history by age and message count, trimmed whole runs at a time so a
tool call never gets separated from its return. Everything older than the most
recent few runs is then elided to its first and last message with a notice in
between saying how many were dropped.

Both read a run's size through ``AgentRun.message_count`` rather than counting
what is loaded: the runtime history loader deliberately fetches older runs down
to two messages, so ``len(run.messages)`` is not how big the run was.

Extracted from the runner because it is policy about the prompt rather than
mechanics of executing a run -- and because the runner is at the architecture
ratchet's size limit.
"""

from __future__ import annotations

import json

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.modules.agent.domain.entities import (
    AgentRun,
    Conversation,
    Message,
    MessageKind,
    MessageRole,
)

#: Opens every message this module synthesizes. Two jobs: the model reads it as
#: scaffolding rather than as something a person said, and the compactor can tell
#: these apart from real user turns so it does not pin them forever.
SYNTHETIC_NOTICE_PREFIX = "[conversation history]"

#: Runs kept with every message. Older runs are elided to first and last.
FULL_HISTORY_AGENT_RUN_COUNT = 5

#: The oldest runs a conversation carries at all, elided ones included.
#:
#: Surface conversations have always had an age and message-count window. Every
#: other kind -- the web UI, tasks, sub-agents -- had none, so a long-lived
#: conversation loaded *every* run it had ever had: a 400-turn one arrives as
#: ~400 elided runs before compaction has seen a single message, and pays for
#: the notice on each of them every turn. Elision bounds a run's size; nothing
#: bounded how many runs there were.
MAX_HISTORY_AGENT_RUNS = 60


def _newest_message_time(run: AgentRun) -> datetime | None:
    """The newest message ``created_at`` in a run as an aware UTC datetime, or
    None when the run has no timestamped messages. Naive timestamps are treated
    as UTC (matching the DM-reset window handling).

    Prefers the run's own ``newest_message_at`` when it has one, because a run
    loaded as a digest carries the timestamp without carrying the messages --
    and a run reduced to its first and last message would otherwise answer from
    the two it kept.
    """
    if run.newest_message_at is not None:
        recorded = run.newest_message_at
        return (
            recorded.replace(tzinfo=timezone.utc)
            if recorded.tzinfo is None
            else recorded
        )
    newest: datetime | None = None
    for message in run.messages:
        created = getattr(message, "created_at", None)
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if newest is None or created > newest:
            newest = created
    return newest


def _capped_run_count(runs: list[AgentRun]) -> list[AgentRun]:
    """The newest runs, however many the conversation actually has.

    Applies to every conversation, surface or not: the oldest runs are the least
    useful and nothing else put a ceiling on how many of them there could be.
    """
    return runs[-MAX_HISTORY_AGENT_RUNS:]


def apply_surface_history_window(
    runs: list[AgentRun], conversation: Conversation | None
) -> list[AgentRun]:
    """Bound how much history a conversation carries.

    Every conversation is capped at ``MAX_HISTORY_AGENT_RUNS`` runs. Surface
    conversations are bounded further, by age + message count.

    Trims at run granularity (a tool-call and its return live in the same run,
    so whole-run trimming never splits a pair) and always keeps at least the
    most recent run. No-op for non-surface conversations or when a limit is
    disabled. Runs are assumed chronologically ordered.
    """
    if not runs:
        return runs
    runs = _capped_run_count(runs)
    metadata = (conversation.metadata or {}) if conversation is not None else {}
    if not metadata.get("surface_platform"):
        return runs

    from app.composition.agent_surface_runtime import surface_history_limits

    max_messages, window_hours = surface_history_limits()

    trimmed = list(runs)
    # Age window: drop whole runs whose newest message predates the window,
    # keeping the most recent run regardless.
    if window_hours > 0 and len(trimmed) > 1:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        kept: list[AgentRun] = []
        for index, run in enumerate(trimmed):
            is_last = index == len(trimmed) - 1
            newest = _newest_message_time(run)
            if is_last or newest is None or newest >= cutoff:
                kept.append(run)
        trimmed = kept or trimmed[-1:]

    # Message-count budget: keep the most recent whole runs that fit, always
    # keeping the most recent run.
    if max_messages > 0:
        result: list[AgentRun] = []
        total = 0
        for run in reversed(trimmed):
            # The run's real size, not how much of it was loaded: an older
            # run arrives holding only its first and last message.
            count = run.message_count
            if result and total + count > max_messages:
                break
            result.insert(0, run)
            total += count
        trimmed = result or trimmed[-1:]

    return trimmed


def runtime_full_run_ids(
    runs: list[AgentRun], conversation: Conversation | None = None
) -> set[UUID]:
    """Which runs need every message, decided the same way the prompt decides.

    Applies the caller's trims first and takes the most recent runs of what
    survives, because the age window is a filter rather than a truncation: a run
    created long ago whose newest message is recent outlives runs created after
    it. Selecting by position on the untrimmed list picks a different set, and
    the run it wrongly elides is then sent short with no notice, because a
    shortened list never reaches the elision branch.
    """
    trimmed = apply_surface_history_window(runs, conversation)
    return {run.id for run in trimmed[-FULL_HISTORY_AGENT_RUN_COUNT:]}


def _dropped_runs_notice(run: AgentRun, dropped: int) -> Message:
    """Say that whole runs are missing, not just the middle of one.

    `_collapsed_run` announces the work it elides; the run caps above it sliced
    silently, so a conversation could lose three hundred runs without a word
    while losing the middle of one said so.
    """
    return Message(
        conversation_id=run.conversation_id,
        sequence=max(0, run.messages[0].sequence - 1) if run.messages else 0,
        agent_run_id=run.id,
        role=MessageRole.USER.value,
        kind=MessageKind.NOTIFICATION,
        text=(
            f"{SYNTHETIC_NOTICE_PREFIX} {dropped} earlier exchange(s) in this "
            "conversation are older than what is carried here and are not shown."
        ),
        metadata={
            "synthetic": True,
            "summary_kind": "conversation_runs_dropped",
            "dropped_run_count": dropped,
        },
    )


def apply_branch_lineage(
    runs: list[AgentRun], current_run: AgentRun | None
) -> list[AgentRun]:
    """Keep only the runs on this run's branch.

    A subthread is a deliberate branch: somebody picked an earlier run and
    carried on from there, which ``AgentRun.parent_run_id`` records. Two
    branches off the same point are separate conversations about the same
    starting position, and each has to be able to not know about the other --
    otherwise branching is just a second way of writing in the same place.

    A run with no parent is on the trunk and sees everything, which is every
    run there has ever been until somebody branches. That is why this is a
    no-op by default rather than a mode.

    Kept: the run's ancestors, and the trunk up to the point it left. Excluded:
    every sibling branch, and anything descended from one.
    """
    if current_run is None or current_run.parent_run_id is None:
        return runs
    by_id = {run.id: run for run in runs}
    lineage: list[AgentRun] = []
    walker: AgentRun | None = current_run
    seen: set[UUID] = set()
    while walker is not None and walker.id not in seen:
        seen.add(walker.id)
        lineage.append(walker)
        parent_id = walker.parent_run_id
        walker = by_id.get(parent_id) if parent_id is not None else None
    root = lineage[-1]
    return [
        run
        for run in runs
        if run.id in seen
        or (run.parent_run_id is None and run.started_at <= root.started_at)
    ]


def _belongs_to_someone_else(
    run: AgentRun,
    *,
    viewer_id: UUID | None,
    owner_id: UUID | None,
) -> bool:
    """Is this run somebody else's working, from this run's point of view?

    A run with no recorded trigger predates the column, and the backfill
    resolved those to the conversation's owner -- so the same rule is applied
    here as in `ConversationRepository.list_messages`, and the model is shown
    what the person reading over its shoulder is shown.

    `viewer_id` of None means nobody asked, which is every single-person
    conversation and every caller that predates this. Nothing is withheld then.
    """
    if viewer_id is None:
        return False
    trigger = run.triggered_by_user_id or owner_id
    return trigger is not None and trigger != viewer_id


def select_runtime_history(
    runs: list[AgentRun],
    conversation: Conversation | None = None,
    *,
    viewer_id: UUID | None = None,
    current_run: AgentRun | None = None,
) -> list[Message]:
    """The history for one run, bounded by age, by count, and by whose it is.

    `viewer_id` is who the run acts for. Another person's run is collapsed to
    its question and its answer however recent it is: the tool arguments carry
    what they asked and the tool results carry data their grants reached, and
    replaying either into this run would hand it both. `_collapsed_run` already
    produces exactly that shape for old runs, so this is the same reduction on
    a different trigger.
    """
    # Surface (Slack/Telegram/WhatsApp/…) conversations bound how much prior
    # history reaches the model by age + count. Trim at run granularity first
    # so tool-call/tool-return pairs (which live within a run) stay intact.
    # Branch first, then the windows: the branch decides which runs are even in
    # this conversation's line, and the caps bound what is left of it.
    runs = apply_branch_lineage(runs, current_run)
    original_count = len(runs)
    runs = apply_surface_history_window(runs, conversation)
    prefix: list[Message] = []
    if runs and len(runs) < original_count:
        prefix = [_dropped_runs_notice(runs[0], original_count - len(runs))]

    owner_id = conversation.user_id if conversation is not None else None
    # Whose words are whose. A conversation can be answered by several agents,
    # and each of them has to read the others as other people.
    names = _agent_names(conversation)
    own_agent_id = getattr(current_run, "agent_id", None) if current_run else None
    # Every run when there are few enough of them: the count cap is what
    # elides, and below it nothing is old enough to lose anything to age.
    recent_run_ids = (
        {run.id for run in runs[-FULL_HISTORY_AGENT_RUN_COUNT:]}
        if len(runs) > FULL_HISTORY_AGENT_RUN_COUNT
        else {run.id for run in runs}
    )
    selected: list[Message] = []
    for run in runs:
        messages = run.ordered_messages()
        if messages:
            selected.extend(
                _messages_for_run(
                    run,
                    messages,
                    viewer_id=viewer_id,
                    owner_id=owner_id,
                    own_agent_id=own_agent_id,
                    names=names,
                    carried_whole=run.id in recent_run_ids,
                )
            )
    return prefix + selected


def _messages_for_run(
    run: AgentRun,
    messages: list[Message],
    *,
    viewer_id: UUID | None,
    owner_id: UUID | None,
    own_agent_id: UUID | None,
    names: dict[UUID, str],
    carried_whole: bool,
) -> list[Message]:
    """How much of one run reaches the prompt, and in whose voice.

    Ordered by what each rule protects. Somebody else's working is withheld
    before anything else, because recency would otherwise wave it through.
    Another agent's speech is attributed next, because replaying it as this
    agent's own is how an agent loses track of who it is. What survives both is
    then bounded by age and size.
    """
    if _belongs_to_someone_else(run, viewer_id=viewer_id, owner_id=owner_id):
        return _collapsed_run(run, messages)
    speaker = names.get(run.agent_id) if run.agent_id != own_agent_id else None
    if speaker:
        return _attributed_to_another_agent(run, messages, speaker)
    if carried_whole or run.message_count <= 2:
        return messages
    return _collapsed_run(run, messages)


def _agent_names(conversation: Conversation | None) -> dict[UUID, str]:
    """Which agent is called what, from the conversation's own roster."""
    participants = getattr(conversation, "participants", None) or []
    return {
        participant.agent_id: participant.display_name
        for participant in participants
        if participant.agent_id is not None and participant.display_name
    }


def _attributed_to_another_agent(
    run: AgentRun, messages: list[Message], name: str
) -> list[Message]:
    """Another agent's turn, rewritten as something that was said to this one.

    Replaying it unchanged is what made a named agent lose track of who it is:
    every assistant message in a conversation was handed to the model as its
    own prior words, so an agent answering after another one read that agent's
    replies as its own and answered as them. One insisted it was Robin while
    running as Batman, in a conversation where Robin had spoken first.

    So the other agent's speech becomes reported speech -- a user-role line
    naming who said it -- and its working is dropped. Dropping the working
    whole is the same rule elision follows: a tool call without its return is
    worse than neither.
    """
    kept: list[Message] = []
    for message in messages:
        if message.role is MessageRole.USER:
            kept.append(message)
            continue
        if message.kind is not MessageKind.TEXT or not message.text:
            continue
        kept.append(
            Message(
                id=message.id,
                conversation_id=message.conversation_id,
                sequence=message.sequence,
                agent_run_id=run.id,
                role=MessageRole.USER.value,
                kind=MessageKind.TEXT,
                text=f"{name} said: {message.text}",
                created_at=message.created_at,
                metadata={"synthetic": True, "spoken_by_agent": name},
            )
        )
    return kept


def _collapsed_run(run: AgentRun, messages: list[Message]) -> list[Message]:
    """An old run reduced to what still matters about it.

    What the person asked for, how much work it took, and what came back.

    Every user message survives verbatim, however old the run. The request is
    the one thing a later turn cannot reconstruct and cannot work without: an
    agent that has lost it does not stop, it invents a plausible substitute from
    whatever context remains and reports that as the thing it was asked for.
    Everything the agent did in between collapses to a single line counting the
    steps, which is all a later turn needs to know about work already finished.

    The run's final message closes it -- that is the answer the user actually
    saw, and the one they may ask about next. It is dropped when it is an
    unpaired tool call, for the reason `_is_unpaired_tool_call` gives: the
    history builder would otherwise tell the model that a side effect which
    succeeded never happened, and instruct it to repeat it.
    """
    kept = [message for message in messages if message.role is MessageRole.USER]
    kept_ids = {message.id for message in kept}

    final = messages[-1]
    include_final = final.id not in kept_ids and not _is_unpaired_tool_call(final)

    # Counted from the run's real size. The loader hands us the user messages
    # plus the run's first and last, so len(messages) would report that almost
    # nothing was skipped.
    skipped_count = max(0, run.message_count - len(kept) - (1 if include_final else 0))

    collapsed = list(kept)
    collapsed.append(
        Message(
            conversation_id=run.conversation_id,
            # Ordered after the last thing kept and before the final answer, so
            # the global sort by sequence puts the notice where it belongs.
            sequence=max(
                kept[-1].sequence if kept else messages[0].sequence,
                final.sequence - 1,
            ),
            agent_run_id=run.id,
            # User role, not system. A `SystemPromptPart` is hoisted by
            # Anthropic to the front of the system prompt, ahead of the whole
            # cacheable prefix -- and this text changes as runs age out, so it
            # invalidated the breakpoint on every turn.
            role=MessageRole.USER.value,
            kind=MessageKind.NOTIFICATION,
            text=(
                f"{SYNTHETIC_NOTICE_PREFIX} earlier agent run summarized: "
                f"worked through {skipped_count} intermediate messages."
            ),
            metadata={
                "synthetic": True,
                "summary_kind": "agent_run_middle_elision",
                "elided_message_count": skipped_count,
            },
        )
    )
    if include_final:
        collapsed.append(_replayable_final(run, messages, final))
    return collapsed


def _replayable_final(
    run: AgentRun, messages: list[Message], final: Message
) -> Message:
    """The run's last message, in a form the history builder can actually replay.

    A pausing run ends on the tool return carrying what the person typed in
    answer to `ask_user`, and that answer lives *only* there -- no user message
    is written for it. Its matching call sits in the middle of the run, which is
    exactly what elision drops, and `_to_pydantic_ai_messages` discards a tool
    return whose call is missing as an orphan.

    So the answer disappeared once the run was six turns back, replaced by
    "worked through N intermediate messages". Carried as a note instead: no
    orphan for the builder to drop, no second query, and the words survive.
    """
    if final.kind is not MessageKind.TOOL_RETURN:
        return final
    call_ids = {
        message.tool_call_id
        for message in messages
        if message.kind is MessageKind.TOOL_CALL
    }
    if final.tool_call_id in call_ids:
        return final
    return Message(
        conversation_id=run.conversation_id,
        sequence=final.sequence,
        agent_run_id=run.id,
        role=MessageRole.USER.value,
        kind=MessageKind.NOTIFICATION,
        text=(
            f"{SYNTHETIC_NOTICE_PREFIX} that run ended with the result of "
            f"{final.tool_name or 'a tool'}: {_short_result(final)}"
        ),
        metadata={
            "synthetic": True,
            "summary_kind": "elided_run_final_tool_return",
            "tool_name": final.tool_name,
        },
    )


#: Long enough for an answer to a question, short enough not to reopen the
#: budget elision exists to protect.
_FINAL_RESULT_MAX_CHARS = 2_000


def _short_result(message: Message) -> str:
    try:
        rendered = json.dumps(message.tool_result, default=str)
    except TypeError, ValueError:  # pragma: no cover - defensive
        rendered = str(message.tool_result)
    if len(rendered) <= _FINAL_RESULT_MAX_CHARS:
        return rendered
    return rendered[:_FINAL_RESULT_MAX_CHARS] + " … [truncated]"


def _is_unpaired_tool_call(message: Message) -> bool:
    """A tool call whose result this elision is about to throw away.

    Eliding a run to its first and last message is fine until the first message
    is an assistant tool call -- which is the normal shape for a run with no
    user message: an approval resume and a snooze wake both create a run and go
    straight into a tool (`pause_resume.start_resume_run_if_ready`).

    Keeping that call without its return is worse than dropping it. The history
    builder pairs calls with returns, finds none, and synthesizes "This tool
    call was interrupted before a result was recorded... Run it again if you
    still need the result." So the model is told a send that *succeeded* never
    happened, and instructed to repeat it -- a duplicate email, a duplicate
    record write.

    Dropping the head instead costs one line of transcript the summary notice
    already accounts for. A pausing tool is never in this position: its return
    is appended to the run it ended, so such a run is two messages long and is
    exempt from elision above.
    """
    return (
        message.role is MessageRole.ASSISTANT and message.kind is MessageKind.TOOL_CALL
    )
