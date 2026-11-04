"""Which messages a turn dispatched to an Agent Host carries in its prompt.

Split from ``remote_payload``, which builds the rest of what a dispatched run
is sent: this is the one decision in it about the conversation, and it reads
nothing but the history the runner assembled.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.queued_messages import STEERED_INTO_RUN
from app.modules.agent.domain.value_objects import MessageKind, MessageRole


def turn_messages(
    messages: Sequence[Message],
    *,
    carries_history: bool,
    resumed_tool_call_id: str | None = None,
    agent_run_id: UUID | None = None,
    session_last_run_id: UUID | None = None,
    session_seen_through: int | None = None,
) -> list[Message]:
    """The messages this prompt has to carry.

    Normally just the latest user message. A Lemma conversation maps to one
    provider session, kept in one working directory, and the agent loads it back
    on every turn — so the rest is already on the other side and resending it
    would only duplicate the conversation in its context.

    A run that resumes a pause is the same rule with a different answer. Waking
    from a ``wait_for`` adds no user message, so "the latest user message" is the
    request that started the task — and re-sending that to an agent whose
    session already contains it does not read as "carry on", it reads as the
    person asking again, so the agent does the work twice. What the session has
    genuinely not seen is the return Lemma synthesized for the call it paused
    on, which is exactly what resuming has to deliver.

    The other exception is a run that is not going to try: a harness that never
    advertised ``loadSession`` has no session to resume, ever. Sending one lone
    message there leaves the agent answering a follow-up it has never seen the
    start of. This costs nothing in the usual case, because a resumable harness
    only lacks a stored session on a conversation's first turn, where there is
    no history to send.

    And a turn answers every message that is its own, not only the newest: the
    one that started it, any that arrived before it was dispatched, and -- for
    a follow-up turn -- everything the person said while the previous turn was
    working, which the follow-up claims (``steered_into_run``). "The latest
    user message" alone answered the last of three and dropped the other two on
    a session that had never seen them.

    And a session has not seen what was written into the conversation in no
    run since its last turn -- a reminder or a report delivered to the chat,
    another chat a merge folded into this one. That is in no turn, so the
    next one carries it (:func:`_written_since_the_session`).
    """
    ordered = sorted(messages, key=lambda item: item.sequence)
    if carries_history:
        return ordered
    carried = {
        message.id
        for message in _own_messages(
            ordered,
            resumed_tool_call_id=resumed_tool_call_id,
            agent_run_id=agent_run_id,
        )
    }
    carried.update(
        message.id
        for message in _written_since_the_session(
            ordered, session_last_run_id, seen_through=session_seen_through
        )
    )
    return [message for message in ordered if message.id in carried]


def _written_since_the_session(
    ordered: Sequence[Message],
    last_run_id: UUID | None,
    *,
    seen_through: int | None = None,
) -> list[Message]:
    """What the history carries in no run that the resumed session has not seen.

    Taken from the history the runner assembled for this turn
    (``runtime_history``), the one the in-process harness is sent whole: so
    they come inside its bounds, a folded chat between its notices, and with
    nothing a stranger's run may not read. A notice shares the sequence of the
    copy it frames; it keeps its side of it because the order is that
    history's.

    New is what follows ``seen_through``, the newest message of the history
    the session's last landed prompt was built from, recorded at its dispatch.

    A session bound before that was kept falls back to the start of
    ``last_run_id``, the newest run whose prompt the session holds -- which
    misses what a follow-up or resume (a run with nothing of its own at
    dispatch) never carried, and is unknown once that run is collapsed out of
    the history. With neither known, all of it is sent: read twice is the
    smaller failure than never read.
    """
    if seen_through is not None:
        return [
            message
            for message in ordered
            if message.agent_run_id is None and message.sequence > seen_through
        ]
    began = min(
        (
            message.sequence
            for message in ordered
            if last_run_id is not None and message.agent_run_id == last_run_id
        ),
        default=None,
    )
    return [
        message
        for message in ordered
        if message.agent_run_id is None and (began is None or message.sequence > began)
    ]


def _own_messages(
    ordered: Sequence[Message],
    *,
    resumed_tool_call_id: str | None,
    agent_run_id: UUID | None,
) -> list[Message]:
    """The messages that are this turn's to answer; see :func:`turn_messages`."""
    resumed = (
        [
            message
            for message in ordered
            if message.kind == MessageKind.TOOL_RETURN
            and message.tool_call_id == resumed_tool_call_id
        ]
        if resumed_tool_call_id is not None
        else []
    )
    if agent_run_id is not None:
        answering = [
            message
            for message in ordered
            if message.role == MessageRole.USER
            and (
                message.agent_run_id == agent_run_id
                or (message.metadata or {}).get(STEERED_INTO_RUN) == str(agent_run_id)
            )
        ]
        if answering:
            # A resume also carries what was said while the pause was being
            # answered: the return the session is waiting on, and the messages.
            return [*resumed, *answering]
    if resumed:
        return resumed
    for message in reversed(ordered):
        if message.role == MessageRole.USER:
            return [message]
    return ordered[-1:]
