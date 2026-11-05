"""Idempotent persistence for caller-reserved conversation IDs."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.entities import Conversation as ConversationEntity
from app.modules.agent.infrastructure.models import ConversationModel


async def create_conversation_for_id(
    session: AsyncSession,
    conversation: ConversationEntity,
) -> tuple[ConversationEntity, bool]:
    """Create once by primary key and validate a repeated reservation."""
    created_id = await session.scalar(
        insert(ConversationModel)
        .values(**ConversationModel.values_from(conversation))
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(ConversationModel.id)
    )
    if created_id is not None:
        created = await session.get(ConversationModel, created_id)
        if created is None:
            raise RuntimeError("Created conversation could not be reloaded")
        return created.to_entity(), True

    existing = await session.get(ConversationModel, conversation.id)
    if existing is None:
        raise RuntimeError("Conversation ID conflict could not be resolved")
    if (
        existing.user_id != conversation.user_id
        or existing.pod_id != conversation.pod_id
        or existing.agent_id != conversation.agent_id
    ):
        raise ValueError(
            "Reserved conversation ID belongs to a different target or owner"
        )
    return existing.to_entity(), False
