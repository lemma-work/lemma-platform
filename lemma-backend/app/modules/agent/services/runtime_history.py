"""What history reaches the model, and how much of a run survives the trip.

Two policies live here, and both apply to every conversation the same way --
where a conversation came from (web, task, Slack, WhatsApp, ...) never changes
what the model is shown. Only the newest ``MAX_HISTORY_AGENT_RUNS`` runs are
carried at all, and everything older than the most recent few is collapsed to
what still matters (every user message, the closing answer) with a notice saying
how many runs were dropped.

Runs are read through ``AgentRun.message_count`` rather than by counting what is
loaded: the runtime history loader deliberately fetches older runs down to two
messages, so ``len(run.messages)`` is not how big the run was.

Extracted from the runner because it is policy about the prompt rather than
mechanics of executing a run -- and because the runner is at the architecture
ratchet's size limit.
"""

from __future__ import annotations

import json

from typing import Protocol
from uuid import UUID

from app.modules.agent.domain.private_notes import is_private_note, run_is_private
from app.modules.agent.domain.entities import (
    AgentRun,
    Message,
    MessageKind,
    MessageRole,
    RuntimeHistoryWindow,
)

#: Opens every message this module synthesizes. Two jobs: the model reads it as
#: scaffolding rather than as something a person said, and the compactor can tell
#: these apart from real user turns so it does not pin them forever.
SYNTHETIC_NOTICE_PREFIX = "[conversation history]"

#: Runs kept with every message. Older runs are elided to first and last.
FULL_HISTORY_AGENT_RUN_COUNT = 5

#: The oldest runs a conversation carries at all, elided ones included.
#:
#: Without a ceiling a long-lived conversation loads *every* run it ever had: a
#: 400-turn one arrives as ~400 elided runs before compaction has seen a single
#: message, and pays for the notice on each of them every turn. Elision bounds a
#: run's size; this bounds how many runs there are.
#:
#: This is deliberately the only bound on history count. A message-count budget
#: over raw messages was tried for surface conversations and lost context: a
#: tool-heavy run is ~100 messages, so one such run exhausted the budget and the
#: model saw only the current message -- while the elision below would have
#: reduced that same run to its user messages and its answer.
MAX_HISTORY_AGENT_RUNS = 60


def cap_history_runs(runs: list[AgentRun]) -> list[AgentRun]:
    """The newest runs, however many the conversation actually has.

    Cuts whole runs, so a tool call and its return -- which live in the same
    run -- are never separated. The most recent run is always kept.
    """
    return runs[-MAX_HISTORY_AGENT_RUNS:]


def bound_runtime_history(
    runs: list[AgentRun],
    *,
    total_runs: int | None = None,
) -> tuple[list[AgentRun], int]:
    """The runs the prompt will carry, and how many were dropped to get there.

    Exists so the trim can happen *before* messages are fetched. The loader used
    to attach messages to every run of the conversation and let
    ``select_runtime_history`` discard what fell outside the window -- so a
    conversation with hundreds of runs paid, on every turn, to read the user
    messages plus the first and last of the runs it was about to throw away.

    The dropped count comes back because it is the one thing the trimmed list no
    longer knows about itself, and the notice announcing those runs to the model
    is built from it. ``total_runs`` is how many the conversation actually has,
    for a caller whose ``runs`` is already a window: without it the count starts
    from the window and the notice under-reports by everything the window took.
    """
    bounded = cap_history_runs(runs)
    return bounded, (len(runs) if total_runs is None else total_runs) - len(bounded)


def runtime_full_run_ids(runs: list[AgentRun]) -> set[UUID]:
    """Which runs need every message, decided the same way the prompt decides.

    Caps first and takes the most recent runs of what survives, so the set is
    picked from the runs the prompt will actually carry.
    """
    return {run.id for run in cap_history_runs(runs)[-FULL_HISTORY_AGENT_RUN_COUNT:]}


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
        role=MessageRole.USER,
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


#: How many run-less notifications ride along with the history. They are short
#: and rarely stack up, so this is a backstop rather than a budget.
MAX_UNATTACHED_NOTIFICATIONS = 20


def oldest_carried_sequence(runs: list[AgentRun]) -> int | None:
    """The sequence of the oldest message the prompt will carry, if any."""
    sequences = [message.sequence for run in runs for message in run.messages]
    return min(sequences) if sequences else None


def first_sequence_of_run(runs: list[AgentRun], run_id: UUID) -> int | None:
    """Where the run being executed begins, if it is among ``runs``."""
    for run in runs:
        if run.id == run_id and run.messages:
            return min(message.sequence for message in run.messages)
    return None


def unattached_notification_window(
    runs: list[AgentRun],
    run_id: UUID,
    *,
    dropped_runs: int,
) -> tuple[int | None, int | None]:
    """``(after, before)`` sequences bounding the run-less notifications to carry.

    The lower bound exists to keep a notification that is older than the
    history out of it, so it applies only when history *was* cut. Applied
    unconditionally it is the oldest message the prompt carries, and for a
    conversation a notification opened that is the first run's own first
    message -- the notification precedes it, so the person's first reply to a
    report or a reminder was read against nothing.

    The upper bound is the turn being answered, whether or not anything was cut.
    """
    after = oldest_carried_sequence(runs) if dropped_runs > 0 else None
    return after, first_sequence_of_run(runs, run_id)


class RuntimeHistorySource(Protocol):
    """The two reads history assembly makes, and nothing else of a repository."""

    async def attach_runtime_history_messages(
        self, runs: list[AgentRun], *, full_run_ids: set[UUID]
    ) -> list[AgentRun]: ...

    async def load_unattached_notifications(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int,
    ) -> list[Message]: ...


async def assemble_runtime_history(
    source: RuntimeHistorySource,
    window: RuntimeHistoryWindow,
    *,
    conversation_id: UUID,
    run_id: UUID,
) -> list[Message]:
    """Every message the model is shown for ``run_id``, in the order it reads them.

    One place for the whole recipe so the runner and anything that wants to know
    what a turn will see cannot drift apart on it -- the notification bounds in
    particular are a decision about *this* history, and were made inline where
    nothing could exercise them without a database.
    """
    # The trim decides which runs need every message, and it can keep an
    # old-but-active run while dropping newer ones -- so it runs before the
    # messages are asked for, and only what survives it gets them. Attaching to
    # the untrimmed list meant a long conversation read hundreds of runs it then
    # discarded.
    bounded, dropped_runs = bound_runtime_history(
        window.runs, total_runs=window.total_runs
    )
    await source.attach_runtime_history_messages(
        bounded, full_run_ids=runtime_full_run_ids(bounded)
    )
    messages = select_runtime_history(bounded, already_dropped=dropped_runs)
    # Belong to no run, so the run-keyed reads above never see them; sequences
    # are conversation-wide, so the harness places them.
    after_sequence, before_sequence = unattached_notification_window(
        bounded, run_id, dropped_runs=dropped_runs
    )
    messages.extend(
        await source.load_unattached_notifications(
            conversation_id,
            after_sequence=after_sequence,
            before_sequence=before_sequence,
            limit=MAX_UNATTACHED_NOTIFICATIONS,
        )
    )
    return messages


def without_private_runs(
    messages: list[Message], runs: list[AgentRun], *, current_run_id: UUID
) -> list[Message]:
    """The history a stranger's run reads: nothing a private note started.

    In a conversation that answers people outside the pod, the member who looks
    after it can write the agent a private note -- "our floor is 40k, don't go
    below it" -- and the run it starts answers in Lemma only. Every later turn
    in the conversation is a stranger's, and a label asking the model not to
    repeat the note is not a boundary. So the note, and everything its run
    said, are not in a stranger's history at all.

    Whole runs go, so a tool call never loses its return. A note queued into
    another run's history is dropped on its own. The current run is never
    dropped: a private run reads its own note.
    """
    private = {
        run.id
        for run in runs
        if run.id != current_run_id and run_is_private(run.metadata)
    }
    return [
        message
        for message in messages
        if message.agent_run_id not in private
        and not (
            message.agent_run_id != current_run_id and is_private_note(message.metadata)
        )
    ]


def select_runtime_history(
    runs: list[AgentRun],
    *,
    already_dropped: int = 0,
) -> list[Message]:
    # Cap at run granularity first so tool-call/tool-return pairs (which live
    # within a run) stay intact.
    #
    # `already_dropped` is what a caller that trimmed before loading messages
    # (see `bound_runtime_history`) took out. The cap below is idempotent, so
    # such a list loses nothing here and would silently lose its notice too.
    original_count = len(runs) + already_dropped
    runs = cap_history_runs(runs)
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
            role=MessageRole.USER,
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
        role=MessageRole.USER,
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
    user message: an approval resume and a wait resolution both create a run and go
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
