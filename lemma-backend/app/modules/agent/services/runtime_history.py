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

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.modules.agent.domain.entities import (
    AgentRun,
    Conversation,
    Message,
    MessageKind,
    MessageRole,
)

#: Runs kept with every message. Older runs are elided to first and last.
FULL_HISTORY_AGENT_RUN_COUNT = 5


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


def apply_surface_history_window(
    runs: list[AgentRun], conversation: Conversation | None
) -> list[AgentRun]:
    """For surface conversations, bound history by age + message count.

    Trims at run granularity (a tool-call and its return live in the same run,
    so whole-run trimming never splits a pair) and always keeps at least the
    most recent run. No-op for non-surface conversations or when a limit is
    disabled. Runs are assumed chronologically ordered.
    """
    if conversation is None or not runs:
        return runs
    metadata = conversation.metadata or {}
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


def select_runtime_history(
    runs: list[AgentRun], conversation: Conversation | None = None
) -> list[Message]:
    # Surface (Slack/Telegram/WhatsApp/…) conversations bound how much prior
    # history reaches the model by age + count. Trim at run granularity first
    # so tool-call/tool-return pairs (which live within a run) stay intact.
    runs = apply_surface_history_window(runs, conversation)
    if len(runs) <= FULL_HISTORY_AGENT_RUN_COUNT:
        return [message for run in runs for message in run.ordered_messages()]

    recent_run_ids = {run.id for run in runs[-FULL_HISTORY_AGENT_RUN_COUNT:]}
    selected: list[Message] = []
    for run in runs:
        messages = run.ordered_messages()
        if not messages:
            continue
        if run.id in recent_run_ids or run.message_count <= 2:
            selected.extend(messages)
            continue

        # Counted from the run's real size. The loader already reduced this
        # run to its first and last message, so len(messages) is 2 here and
        # would report nothing was skipped.
        skipped_count = max(0, run.message_count - 2)
        if not _is_unpaired_tool_call(messages[0]):
            selected.append(messages[0])
        selected.append(
            Message(
                conversation_id=run.conversation_id,
                sequence=messages[0].sequence,
                agent_run_id=run.id,
                role=MessageRole.SYSTEM.value,
                kind=MessageKind.NOTIFICATION,
                text=(
                    "Earlier agent run summarized: "
                    f"worked through {skipped_count} intermediate messages."
                ),
                metadata={
                    "synthetic": True,
                    "summary_kind": "agent_run_middle_elision",
                    "elided_message_count": skipped_count,
                },
            )
        )
        selected.append(messages[-1])
    return selected


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
        message.role is MessageRole.ASSISTANT
        and message.kind is MessageKind.TOOL_CALL
    )
