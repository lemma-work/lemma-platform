"""Where a conversation sits, for a reader that has only its id.

``AgentRunCompletedEvent`` now carries this context on the event itself, captured
by the finalizer that already holds it. This is the fallback for the runs that
finish somewhere else: the stop-request handler and the two sweeps in
``conversation_status_repair`` end a run from a row, not from a live
``RunIdentity``, and have no pod or organization to put on the event. Redelivered
events published before those fields existed land here too.

Four scalars rather than the ``Conversation`` entity, which is what the
composition root used to hand out. Publishing the row makes every field on it
part of this surface by default, and the caller then reads whichever ones it
notices -- which is how ``created_at`` ended up standing in for a run's duration.

A submodule rather than ``contracts/__init__``, which is a leaf: this reaches the
repository layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.infrastructure.repositories import ConversationRepository


@dataclass(frozen=True, slots=True)
class ConversationScope:
    """Whose conversation it is, and which pod and agent it belongs to."""

    user_id: UUID
    pod_id: UUID
    organization_id: UUID | None
    #: Absent for the pod's own assistant, which names nobody.
    agent_id: UUID | None


async def conversation_scope(uow, conversation_id: UUID) -> ConversationScope | None:
    """This conversation's placement, or ``None`` if it has since been deleted."""
    conversation = await ConversationRepository(uow).get_conversation(conversation_id)
    if conversation is None:
        return None
    return ConversationScope(
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
        organization_id=conversation.organization_id,
        agent_id=conversation.agent_id,
    )


__all__ = ["ConversationScope", "conversation_scope"]
