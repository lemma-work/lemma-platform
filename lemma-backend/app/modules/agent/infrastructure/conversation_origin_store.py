"""Idempotent persistence for conversations created by durable invocations."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.entities import Conversation as ConversationEntity
from app.modules.agent.infrastructure.models import ConversationModel


async def create_conversation_for_origin(
    session: AsyncSession,
    conversation: ConversationEntity,
) -> tuple[ConversationEntity, bool]:
    if conversation.origin_type is None or conversation.origin_id is None:
        raise ValueError("origin_type and origin_id are required")

    values = {
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
    created_id = await session.scalar(
        insert(ConversationModel)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(ConversationModel.id)
    )
    if created_id is not None:
        created = await session.get(ConversationModel, created_id)
        if created is None:
            raise RuntimeError("Created conversation could not be reloaded")
        return created.to_entity(), True

    existing = await session.scalar(
        select(ConversationModel).where(
            ConversationModel.origin_type == conversation.origin_type,
            ConversationModel.origin_id == conversation.origin_id,
        )
    )
    if existing is None:
        raise RuntimeError("Conversation origin conflict could not be resolved")
    return existing.to_entity(), False
