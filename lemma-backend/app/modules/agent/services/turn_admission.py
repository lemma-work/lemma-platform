"""What a new message carries, and what it has to wait behind.

`TurnCoordinator.start` decides whether a message joins a run, starts one, or
waits. This is the part of that decision that is about the message rather than
the lock: the metadata it is stored with, and the pause that holds it.

A pause holds a message in two cases:

- **Its answer is on its way.** The person approved, and the approved tool is
  running in a worker (its ``agent.approval.execute`` item). A run started now
  would rebuild a history in which the call is unanswered, be told "the user
  did not answer this", and never see the result -- the model asks again, and
  the action can run twice. So the message waits for the resume run, which
  carries both.
- **The sender is not the person who was asked.** Replies arriving for
  `message_user` are news, not an answer, and starting a turn would deny a
  question the owner has not decided. A message the person types does supersede
  an open question; that is still `start`'s rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pausing_tools import USER_PAUSING_TOOL_NAMES
from app.modules.agent.domain.conversation_ports import (
    ConversationStatusWrite,
    PausesHoldingATurn,
)
from app.modules.agent.domain.private_notes import (
    NOTE_IN_DM_KEY,
    WRITTEN_IN_LEMMA_KEY,
    is_private_note,
    note_in_member_dm,
    written_in_lemma_into,
)
from app.modules.agent.domain.queued_messages import (
    CLIENT_MESSAGE_ID,
    DURING_ACTIVE_RUN,
    SERVER_OWNED_KEYS,
)
from app.modules.agent.domain.value_objects import ConversationStatus


def stamped(
    message_metadata: dict[str, object] | None,
    conversation: Conversation,
    *,
    typed_in_lemma: bool,
) -> dict[str, object] | None:
    """The caller's metadata, with where it was written decided by the server.

    A client never says it: the mark changes who the model takes the writer to
    be, so a chat platform's payload or a client's body claiming it is dropped.
    """
    if not message_metadata and not typed_in_lemma:
        return message_metadata
    marked = dict(message_metadata or {})
    marked.pop(WRITTEN_IN_LEMMA_KEY, None)
    written_in = (
        written_in_lemma_into(conversation.metadata) if typed_in_lemma else None
    )
    if written_in is not None:
        marked[WRITTEN_IN_LEMMA_KEY] = written_in
    return marked or message_metadata


def user_message_metadata(
    message_metadata: dict[str, object] | None,
    conversation: Conversation,
    *,
    joins_a_run: bool,
    client_message_id: str | None,
) -> dict[str, object]:
    """What a user message is stored with.

    The server's keys go on *after* the caller's are filtered, not before: a
    surface builds this dict out of webhook fields and a client sends it in a
    request body, and neither gets a vote on which run the message belongs to,
    whether it has been delivered, or which send it is a retry of.
    """
    metadata = {
        key: value
        for key, value in (message_metadata or {}).items()
        if key not in SERVER_OWNED_KEYS
        and key not in {"author_user_id", "agent_run_id", NOTE_IN_DM_KEY}
    }
    metadata[DURING_ACTIVE_RUN] = joins_a_run
    if client_message_id is not None:
        metadata[CLIENT_MESSAGE_ID] = client_message_id
    if is_private_note(metadata) and note_in_member_dm(conversation.metadata):
        metadata[NOTE_IN_DM_KEY] = True
    return metadata


@dataclass(frozen=True, slots=True)
class HeldTurn:
    """The pause a message waits behind instead of starting a run."""

    #: The run that paused; the message is queued behind it, and the run that
    #: resumes it claims the message.
    agent_run_id: UUID


async def pause_holding_the_turn(
    repository: PausesHoldingATurn,
    conversation_id: UUID,
    *,
    include_undecided: bool,
) -> HeldTurn | None:
    """The pause a message must wait behind, if any. Caller holds the lock."""
    in_flight = await repository.pausing_calls_decided_awaiting_a_return(
        conversation_id=conversation_id,
        pausing_tool_names=USER_PAUSING_TOOL_NAMES,
    )
    undecided = (
        await repository.pausing_calls_awaiting_a_decision(
            conversation_id=conversation_id,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )
        if include_undecided
        else []
    )
    holding = [call for call in (*in_flight, *undecided) if call.agent_run_id]
    if not holding:
        return None
    newest = max(holding, key=lambda call: call.sequence)
    assert newest.agent_run_id is not None
    return HeldTurn(agent_run_id=newest.agent_run_id)


async def hold_behind(
    repository: ConversationStatusWrite,
    conversation: Conversation,
) -> None:
    """Leave the conversation waiting on the pause the message is held behind.

    WAITING even after Stop: Stop keeps an already-decided approval from waking
    the agent, but a message sent afterwards asks for an answer, and the resume
    is what will carry it. Nothing has to be re-queued for the decided ones:
    each decision wrote its ``agent.approval.execute`` item in its own
    transaction, and that item always ends with a return.
    """
    if conversation.status != ConversationStatus.WAITING:
        await repository.set_conversation_status(
            conversation_id=conversation.id, status=ConversationStatus.WAITING
        )
        conversation.status = ConversationStatus.WAITING
