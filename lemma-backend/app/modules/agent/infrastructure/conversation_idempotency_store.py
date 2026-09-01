"""Idempotent persistence for caller-reserved conversation IDs."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.entities import Conversation as ConversationEntity
from app.modules.agent.infrastructure.conversation_participant_store import (
    ensure_owner_participant,
)
from app.modules.agent.infrastructure.models import ConversationModel


def _conversation_values(conversation: ConversationEntity) -> dict:
    return {
        "id": conversation.id,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "user_id": conversation.user_id,
        "pod_id": conversation.pod_id,
        "organization_id": conversation.organization_id,
        "agent_id": conversation.agent_id,
        "title": conversation.title,
        "instructions": conversation.instructions,
        "agent_runtime": (
            conversation.agent_runtime.model_dump(mode="json")
            if conversation.agent_runtime
            else None
        ),
        "origin_type": conversation.origin_type,
        "origin_id": conversation.origin_id,
        "conversation_type": conversation.type.value,
        "status": conversation.status.value if conversation.status else None,
        "output_data": conversation.output,
        "parent_id": conversation.parent_id,
        "conversation_metadata": conversation.metadata,
    }


async def create_conversation_for_id(
    session: AsyncSession,
    conversation: ConversationEntity,
) -> tuple[ConversationEntity, bool]:
    """Create once by primary key and validate a repeated reservation."""
    created_id = await session.scalar(
        insert(ConversationModel)
        .values(**_conversation_values(conversation))
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(ConversationModel.id)
    )
    if created_id is not None:
        created = await session.get(ConversationModel, created_id)
        if created is None:
            raise RuntimeError("Created conversation could not be reloaded")
        # The row's own owner, not the caller's id: this path can return a
        # conversation somebody else reserved.
        await ensure_owner_participant(
            session, conversation_id=created.id, user_id=created.user_id
        )
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
