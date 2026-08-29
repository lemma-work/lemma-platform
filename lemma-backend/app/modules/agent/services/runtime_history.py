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


def select_runtime_history(
    runs: list[AgentRun], conversation: Conversation | None = None
) -> list[Message]:
    # Surface (Slack/Telegram/WhatsApp/…) conversations bound how much prior
    # history reaches the model by age + count. Trim at run granularity first
    # so tool-call/tool-return pairs (which live within a run) stay intact.
    original_count = len(runs)
    runs = apply_surface_history_window(runs, conversation)
    prefix: list[Message] = []
    if runs and len(runs) < original_count:
        prefix = [_dropped_runs_notice(runs[0], original_count - len(runs))]
    if len(runs) <= FULL_HISTORY_AGENT_RUN_COUNT:
        return prefix + [message for run in runs for message in run.ordered_messages()]

    recent_run_ids = {run.id for run in runs[-FULL_HISTORY_AGENT_RUN_COUNT:]}
    selected: list[Message] = []
    for run in runs:
        messages = run.ordered_messages()
        if not messages:
            continue
        if run.id in recent_run_ids or run.message_count <= 2:
            selected.extend(messages)
            continue
        selected.extend(_collapsed_run(run, messages))
    return prefix + selected


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
