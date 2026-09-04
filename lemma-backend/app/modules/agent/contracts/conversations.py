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

from app.modules.agent.infrastructure.models import ConversationModel
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


async def merge_conversation_metadata(
    uow, conversation_id: UUID, updates: dict[str, object]
) -> None:
    """Fold these keys into a conversation's metadata, leaving the rest alone.

    A write, and the only one another module makes to this row. `agent_surfaces`
    was doing it by loading `ConversationModel` from `app/composition/surface_agent.py`
    and assigning the column itself, which put the read-modify-write -- and the
    fact that the column is named `conversation_metadata` and not `metadata` --
    inside a module that owns neither.

    A no-op for a conversation that has since been deleted: the caller is
    stamping a surface's delivery details onto a run that has already finished,
    and there is nothing to repair if the run's conversation is gone.
    """
    model = await uow.session.get(ConversationModel, conversation_id)
    if model is None:
        return
    metadata = dict(model.conversation_metadata or {})
    metadata.update(updates)
    model.conversation_metadata = metadata
    await uow.session.flush()


__all__ = [
    "ConversationScope",
    "conversation_scope",
    "merge_conversation_metadata",
]
