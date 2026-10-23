"""What history reaches the model, and how much of a run survives the trip.

Two policies live here, and both apply to every conversation the same way --
where a conversation came from (web, task, Slack, WhatsApp, ...) never changes
what the model is shown. Only the newest runs, up to ``MAX_HISTORY_AGENT_RUNS``,
are carried at all, and everything older than the most recent few is collapsed
to what still matters (every user message, the closing answer) with a notice
saying how many runs were dropped.

Both move in fixed blocks of ``ELISION_BLOCK_RUNS``, counted from the start of
the conversation, never one run at a time. A provider caches the request
prefix, and the history is most of it: collapsing the oldest full run on every
new run rewrote the history inside the cached prefix on every run, and dropping
the oldest carried run every run past the cap left nothing cacheable at all.
Block-aligned, consecutive runs share the whole history up to the newest run,
and the prefix is rewritten once every few runs instead of on each.

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

from app.modules.agent.domain.folded_turns import framed_folds, newest_folds
from app.modules.agent.domain.history_notices import SYNTHETIC_NOTICE_PREFIX
from app.modules.agent.domain.private_notes import is_private_note, run_is_private
from app.modules.agent.domain.queued_messages import STOP_REQUESTED_AFTER_SEQUENCE
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.domain.entities import (
    AgentRun,
    Message,
    MessageKind,
    MessageRole,
    RuntimeHistoryWindow,
)

#: At least this many of the newest runs keep every message. Older runs are
#: elided to what still matters about them.
FULL_HISTORY_AGENT_RUN_COUNT = 5

#: Runs are elided, and dropped, this many at a time. Between two boundaries
#: the history only grows at its end, so every run shares its predecessor's
#: whole history as a cached prefix.
ELISION_BLOCK_RUNS = 5

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


def _block_start(total_runs: int, *, keep: int) -> int:
    """The first run, counted from the conversation's start, of a block-aligned
    tail that keeps at least ``keep`` runs."""
    return max(0, (total_runs - keep) // ELISION_BLOCK_RUNS * ELISION_BLOCK_RUNS)


def first_carried_run(total_runs: int) -> int:
    """How many of the conversation's oldest runs are not carried at all.

    A multiple of the block size, so the carried tail holds between
    ``MAX_HISTORY_AGENT_RUNS - ELISION_BLOCK_RUNS`` and one short of
    ``MAX_HISTORY_AGENT_RUNS`` runs -- never more than the loader reads.
    """
    return _block_start(total_runs, keep=MAX_HISTORY_AGENT_RUNS - ELISION_BLOCK_RUNS)


def first_full_run(total_runs: int) -> int:
    """The oldest run, counted from the conversation's start, kept whole."""
    return _block_start(total_runs, keep=FULL_HISTORY_AGENT_RUN_COUNT)


def cap_history_runs(
    runs: list[AgentRun], *, total_runs: int | None = None
) -> list[AgentRun]:
    """The newest runs, however many the conversation actually has.

    Cuts whole runs, so a tool call and its return -- which live in the same
    run -- are never separated. The most recent run is always kept.
    ``total_runs`` is how many the conversation has, when ``runs`` is only the
    newest of them: the cut is counted from the conversation's start.
    """
    total = len(runs) if total_runs is None else total_runs
    offset = total - len(runs)
    return runs[max(0, first_carried_run(total) - offset) :]


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
    total = len(runs) if total_runs is None else total_runs
    bounded = cap_history_runs(runs, total_runs=total)
    return bounded, total - len(bounded)


def runtime_full_run_ids(
    runs: list[AgentRun], *, already_dropped: int = 0
) -> set[UUID]:
    """Which runs need every message, decided the same way the prompt decides.

    ``runs`` is the conversation's newest runs with ``already_dropped`` older
    ones before them, so each run's place in the conversation is known.
    """
    total = already_dropped + len(runs)
    first_full = first_full_run(total)
    return {
        run.id
        for index, run in enumerate(runs, start=already_dropped)
        if index >= first_full
    }


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


#: How many notifications and held messages ride along with the history, the
#: newest kept. A backstop rather than a budget: notifications rarely stack up,
#: and where lines do -- a stretch a member answered by hand, where both sides
#: are run-less -- the agent taking the conversation back picks up from the newest.
MAX_UNATTACHED_MESSAGES = 20

#: How many copies a contact merge folded in (``folded_turns``) ride along, the
#: newest kept. Their own bound, not a share of the one above: a long chat
#: folded in would take every place in it, and the notifications would go.
MAX_FOLDED_MESSAGES = 40


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


def unattached_message_window(
    runs: list[AgentRun],
    run_id: UUID,
    *,
    dropped_runs: int,
) -> tuple[int | None, int | None]:
    """``(after, before)`` sequences bounding the run-less messages to carry.

    The lower bound exists to keep a message that is older than the history
    out of it, so it applies only when history *was* cut. Applied
    unconditionally it is the oldest message the prompt carries, and for a
    conversation a notification opened that is the first run's own first
    message -- the notification precedes it, so the person's first reply to a
    report or a reminder was read against nothing. A contact whose first words
    were held for a person is the same.

    The upper bound is the turn being answered, whether or not anything was cut.
    """
    after = oldest_carried_sequence(runs) if dropped_runs > 0 else None
    return after, first_sequence_of_run(runs, run_id)


class ChannelReader(Protocol):
    """Reads the channel a history's messages refer to into them
    (``channel_background``): the background of the runs named, and every
    member's follow-up."""

    async def __call__(
        self, messages: list[Message], *, background_of_runs: set[UUID]
    ) -> list[Message]: ...


class RuntimeHistorySource(Protocol):
    """The two reads history assembly makes, and nothing else of a repository."""

    async def attach_runtime_history_messages(
        self, runs: list[AgentRun], *, full_run_ids: set[UUID]
    ) -> list[AgentRun]: ...

    async def load_unattached_messages(
        self,
        conversation_id: UUID,
        *,
        after_sequence: int | None,
        before_sequence: int | None,
        limit: int,
        folded_limit: int,
    ) -> list[Message]: ...


async def assemble_runtime_history(
    source: RuntimeHistorySource,
    window: RuntimeHistoryWindow,
    *,
    conversation_id: UUID,
    run_id: UUID,
    read_channel: ChannelReader | None = None,
) -> list[Message]:
    """Every message the model is shown for ``run_id``, in the order it reads them.

    One place for the whole recipe so the runner and anything that wants to know
    what a turn will see cannot drift apart on it -- the bounds on run-less
    messages in particular are a decision about *this* history, and were made
    inline where nothing could exercise them without a database. Those ride
    along with the runs: notifications, what somebody outside the pod said
    while a person had them (``held_turns``), and what a contact merge copied
    in from another of their chats, read as that chat's (``folded_turns``).
    """
    # The trim decides which runs need every message, and it can keep an
    # old-but-active run while dropping newer ones -- so it runs before the
    # messages are asked for, and only what survives it gets them. Attaching to
    # the untrimmed list meant a long conversation read hundreds of runs it then
    # discarded.
    bounded, dropped_runs = bound_runtime_history(
        window.runs, total_runs=window.total_runs
    )
    full_run_ids = runtime_full_run_ids(bounded, already_dropped=dropped_runs)
    await source.attach_runtime_history_messages(bounded, full_run_ids=full_run_ids)
    messages = select_runtime_history(bounded, already_dropped=dropped_runs)
    # Belong to no run, so the run-keyed reads above never see them; sequences
    # are conversation-wide, so the harness places them.
    after_sequence, before_sequence = unattached_message_window(
        bounded, run_id, dropped_runs=dropped_runs
    )
    unattached, folds_cut = newest_folds(
        await source.load_unattached_messages(
            conversation_id,
            after_sequence=after_sequence,
            before_sequence=before_sequence,
            limit=MAX_UNATTACHED_MESSAGES,
            # One past the bound, to know whether it left any out.
            folded_limit=MAX_FOLDED_MESSAGES + 1,
        ),
        keep=MAX_FOLDED_MESSAGES,
    )
    messages.extend(unattached)
    if read_channel is not None:
        # A run cut to its requests keeps none of what it was shown around them.
        messages = await read_channel(
            messages, background_of_runs=full_run_ids | {run_id}
        )
    # After the channel is read in, which drops a copied follow-up whose line
    # is gone: a stretch of copies is framed as what is left of it.
    return framed_folds(messages, cut=folds_cut)


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
    dropped: a private run reads its own note. What the stranger said while a
    person had them stays: it is in no run, and never a note (``held_turns``).
    So does what a merge copied in of another of their chats, which is only
    what they saw of it (``folded_turns``).
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
    # (see `bound_runtime_history`) took out. Counted into the total, the cap
    # below takes nothing more from such a list, and its notice survives.
    total = len(runs) + already_dropped
    runs = cap_history_runs(runs, total_runs=total)
    dropped = total - len(runs)
    prefix: list[Message] = []
    if runs and dropped:
        prefix = [_dropped_runs_notice(runs[0], dropped)]
    recent_run_ids = runtime_full_run_ids(runs, already_dropped=dropped)
    selected: list[Message] = []
    for run in runs:
        messages = run.ordered_messages()
        if not messages:
            continue
        if run.id in recent_run_ids or run.message_count <= 2:
            kept = messages
        else:
            kept = _collapsed_run(run, messages)
        if run.status is AgentRunStatus.STOPPED:
            kept = _closed_by_stop(run, kept)
        selected.extend(kept)
    return prefix + selected


#: What a stopped run's history ends on. A run stopped half way ends on a
#: request and the tool calls made for it, with nothing saying it is over, and
#: the next run read that as work still to finish: asked "what is 2+2?" after a
#: stopped essay, it wrote the essay and said it was done.
STOPPED_BY_MEMBER = (
    f"{SYNTHETIC_NOTICE_PREFIX} This request was stopped by the member before it "
    "finished. It is stopped: do not continue it, finish it or report it as "
    "done. Answer only what is asked after this; do it again only if asked again."
)


def _closed_by_stop(run: AgentRun, messages: list[Message]) -> list[Message]:
    """``messages`` with the stop said where it happened.

    After the work the run did, and before anything said after Stop was
    pressed: those lines ride on the stopped run and are what the next run
    answers, so they read as coming after the stop, not as part of what
    was stopped.
    """
    stop_after = (run.metadata or {}).get(STOP_REQUESTED_AFTER_SEQUENCE)
    if isinstance(stop_after, bool) or not isinstance(stop_after, int):
        stop_after = None
    cut = len(messages)
    if stop_after is not None:
        while (
            cut
            and messages[cut - 1].role is MessageRole.USER
            and messages[cut - 1].sequence > stop_after
        ):
            cut -= 1
    if not cut:
        return messages
    marker = Message(
        conversation_id=run.conversation_id,
        # Ties the last message it follows; the history is sorted stably, so
        # it stays after it.
        sequence=messages[cut - 1].sequence,
        agent_run_id=run.id,
        role=MessageRole.USER,
        kind=MessageKind.NOTIFICATION,
        text=STOPPED_BY_MEMBER,
        metadata={"synthetic": True, "summary_kind": "agent_run_stopped"},
    )
    return [*messages[:cut], marker, *messages[cut:]]


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
