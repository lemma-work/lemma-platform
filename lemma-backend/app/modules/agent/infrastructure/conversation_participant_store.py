"""Reading and writing conversation membership.

Kept out of ``ConversationRepository`` for the reason its other query mixins
are: that class is near the per-file limit, and membership is a self-contained
question that nothing else in it needs to know the shape of.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.participants import (
    ConversationParticipant,
    ConversationParticipantRole,
)
from app.composition.conversation_participant_labels import read_user_labels
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.participant_models import (
    ConversationParticipantModel,
)


async def ensure_owner_participant(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID,
) -> None:
    """Record the opener's membership for a conversation that has none.

    Called from every path that creates a conversation, and idempotent because
    two of those three are ``on_conflict_do_nothing`` inserts that return an
    existing row as readily as a new one.

    ``user_id`` must be the conversation row's own ``user_id``, never the
    caller's. On the idempotent paths the row that comes back may already
    belong to somebody else, and writing the caller in as owner there would
    hand them a conversation they have no claim to.
    """
    await session.execute(
        insert(ConversationParticipantModel)
        .values(
            conversation_id=conversation_id,
            user_id=user_id,
            role=ConversationParticipantRole.OWNER.value,
        )
        .on_conflict_do_nothing(constraint="uq_conversation_participant_user")
    )


async def list_participants(
    session: AsyncSession,
    conversation_id: UUID,
) -> list[ConversationParticipant]:
    """Everyone in one conversation, people and agents alike, with their names.

    One query with two outer joins rather than a roster and then a lookup per
    row: this is read on every conversation open, and a transcript cannot put a
    name on a turn until it has come back.
    """
    result = await session.execute(
        select(ConversationParticipantModel, AgentModel.name)
        .outerjoin(AgentModel, ConversationParticipantModel.agent_id == AgentModel.id)
        .where(ConversationParticipantModel.conversation_id == conversation_id)
        .order_by(ConversationParticipantModel.created_at)
    )
    rows = list(result)
    participants: list[ConversationParticipant] = []
    for model, agent_name in rows:
        entity = model.to_entity()
        entity.display_name = agent_name
        participants.append(entity)
    # People's names live in identity, which this module may not read directly.
    # Resolved together afterwards rather than joined: the join would be one
    # query, and one query that crosses a module boundary is worse than two
    # that do not.
    labels = await read_user_labels(
        session,
        [
            participant.user_id
            for participant in participants
            if participant.user_id is not None
        ],
    )
    for participant in participants:
        if participant.user_id is not None:
            participant.display_name = labels.get(participant.user_id)
    return participants


async def list_participants_for_conversations(
    session: AsyncSession,
    conversation_ids: list[UUID],
) -> dict[UUID, list[ConversationParticipant]]:
    """The same, for many conversations in one round trip.

    A list endpoint that asked per conversation would issue one query per row,
    which is the shape that makes a sidebar slow as somebody's history grows.
    """
    if not conversation_ids:
        return {}
    result = await session.execute(
        select(ConversationParticipantModel)
        .where(ConversationParticipantModel.conversation_id.in_(conversation_ids))
        .order_by(ConversationParticipantModel.created_at)
    )
    grouped: dict[UUID, list[ConversationParticipant]] = {}
    for model in result.scalars():
        grouped.setdefault(model.conversation_id, []).append(model.to_entity())
    return grouped


async def add_participant(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
    role: ConversationParticipantRole = ConversationParticipantRole.MEMBER,
) -> ConversationParticipant:
    """Add one person or one agent. Adding twice is not an error.

    Conflict-free rather than try/except: catching the integrity error would
    mean rolling the session back, and the session belongs to the caller's unit
    of work, not to this function.
    """
    if (user_id is None) == (agent_id is None):
        raise ValueError("a participant is exactly one of a user or an agent")
    constraint = (
        "uq_conversation_participant_user"
        if user_id is not None
        else "uq_conversation_participant_agent"
    )
    await session.execute(
        insert(ConversationParticipantModel)
        .values(
            conversation_id=conversation_id,
            user_id=user_id,
            agent_id=agent_id,
            role=role.value,
        )
        .on_conflict_do_nothing(constraint=constraint)
    )
    stored = await _find_participant(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    if stored is None:
        raise RuntimeError("Participant could not be read back after insert")
    return stored.to_entity()


async def remove_participant(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
) -> bool:
    """Remove one participant. Returns whether there was one to remove.

    The owner is refused rather than silently skipped: a caller asking to evict
    the person whose conversation it is has got something wrong, and returning
    False would read as "they were not in it".
    """
    if (user_id is None) == (agent_id is None):
        raise ValueError("a participant is exactly one of a user or an agent")
    existing = await _find_participant(
        session,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    if existing is None:
        return False
    if existing.role == ConversationParticipantRole.OWNER.value:
        raise ValueError("the owner of a conversation cannot be removed from it")
    await session.execute(
        delete(ConversationParticipantModel).where(
            ConversationParticipantModel.id == existing.id
        )
    )
    return True


async def _find_participant(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: UUID | None,
    agent_id: UUID | None,
) -> ConversationParticipantModel | None:
    subject = (
        ConversationParticipantModel.user_id == user_id
        if user_id is not None
        else ConversationParticipantModel.agent_id == agent_id
    )
    return await session.scalar(
        select(ConversationParticipantModel).where(
            ConversationParticipantModel.conversation_id == conversation_id,
            subject,
        )
    )
