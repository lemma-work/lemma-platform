"""What a new message carries, and what it has to wait behind.

`TurnCoordinator.start` decides whether a message joins a run, starts one, or
waits. This is the part of that decision that is about the message rather than
the lock: what it is stored with, and the pause that holds it.

A pause holds a message in two cases:

- **Its answer is on its way.** The person approved, and the approved tool is
  running in a worker (its ``agent.approval.execute`` item). A run started now
  would rebuild a history in which the call is unanswered, be told "the user
  did not answer this", and never see the result -- the model asks again, and
  the action can run twice. So the message waits for the resume run, which
  carries both.
- **The sender is not the person who was asked.** Replies arriving for
  `channel_post` are news, not an answer, and starting a turn would deny a
  question the owner has not decided. A message the person types does supersede
  an open question; that is still `start`'s rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.pausing_tools import USER_PAUSING_TOOL_NAMES
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.domain.private_notes import (
    note_in_member_dm,
    requested_private_note,
    written_in_lemma_into,
)
from app.modules.agent.domain.queued_messages import SERVER_OWNED_KEYS
from app.modules.agent.domain.value_objects import ConversationStatus


def admitted_state(
    message_metadata: dict[str, object] | None,
    conversation: Conversation,
    *,
    typed_in_lemma: bool,
    client_message_id: str | None,
) -> MessageState:
    """What the server records about a user message, decided by the server.

    A note is the one mark the caller asks for, and it is read off the request
    here, once. Where the message was written is never the caller's to say:
    the mark changes who the model takes the writer to be, so it comes from
    whether a person typed it in Lemma, and into what.
    """
    private_note = requested_private_note(message_metadata)
    return MessageState(
        client_message_id=client_message_id,
        private_note=private_note,
        private_note_in_dm=private_note and note_in_member_dm(conversation),
        written_in=written_in_lemma_into(conversation) if typed_in_lemma else None,
    )


def user_message_metadata(
    message_metadata: dict[str, object] | None,
) -> dict[str, object]:
    """What a user message keeps of what its caller passed as metadata.

    A surface builds this dict out of webhook fields and a client sends it in
    a request body, and neither gets a vote on which run the message belongs
    to, whether it has been delivered, or which send it is a retry of: every
    key the server keeps in a column is dropped.
    """
    return {
        key: value
        for key, value in (message_metadata or {}).items()
        if key not in SERVER_OWNED_KEYS
    }


def message_source(message_metadata: dict[str, object] | None) -> str | None:
    """The ``source`` its caller labelled a message with, if any."""
    source = (message_metadata or {}).get("source")
    return source if isinstance(source, str) and source else None


@dataclass(frozen=True, slots=True)
class HeldTurn:
    """The pause a message waits behind instead of starting a run."""

    #: The run that paused; the message is queued behind it, and the run that
    #: resumes it claims the message.
    agent_run_id: UUID


async def pause_holding_the_turn(
    repository: Pauses,
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
    repository: Conversations,
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
