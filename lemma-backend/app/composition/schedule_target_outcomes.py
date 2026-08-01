"""Authoritative target-state reads used by schedule outcome consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.repositories import ConversationRepository


@dataclass(frozen=True, slots=True)
class AgentConversationOutcome:
    status: str
    completed_at: datetime


async def resolve_agent_conversation_outcome(
    uow: SqlAlchemyUnitOfWork,
    conversation_id: UUID,
) -> AgentConversationOutcome | None:
    conversation = await ConversationRepository(uow).get_conversation(conversation_id)
    if conversation is None or conversation.status is None:
        return None
    return AgentConversationOutcome(
        status=conversation.status.value,
        completed_at=conversation.updated_at,
    )
