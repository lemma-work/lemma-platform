"""A contact's conversations, for the module that forgets and exports contacts.

A contact's conversation is marked on its metadata (``domain/outsiders``), and
that mark is how these find them. Deleting cascades to its runs, messages,
waits, approvals and routing links; notifications that pointed at it keep
their text and lose the pointer.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, delete, select, update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.outsiders import (
    AUDIENCE_KEY,
    CONTACT,
    CONTACT_KEY,
    OUTSIDERS,
)
from app.modules.agent.domain.private_notes import is_private_note, run_is_private
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)

#: The most conversations one export carries.
MAX_EXPORTED_CONVERSATIONS = 500

#: The most messages one conversation contributes to an export.
MAX_EXPORTED_MESSAGES = 2000


class ExportedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    text: str
    created_at: datetime
    sequence: int


class ExportedConversation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str | None
    created_at: datetime
    messages: tuple[ExportedMessage, ...]


def _theirs(pod_id: UUID, contact_id: UUID) -> tuple[ColumnElement[bool], ...]:
    # Containment, so the metadata's GIN index answers it.
    return (
        ConversationModel.pod_id == pod_id,
        ConversationModel.conversation_metadata.contains(
            {AUDIENCE_KEY: CONTACT, CONTACT_KEY: str(contact_id)}
        ),
    )


async def forget_contact_conversations(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> int:
    """Delete every conversation this pod had with this contact."""
    result = await uow.session.execute(
        delete(ConversationModel).where(*_theirs(pod_id, contact_id))
    )
    return int(result.rowcount or 0)


async def export_contact_conversations(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> list[ExportedConversation]:
    """What was said with this contact: their messages and the bot's answers.

    Text only. Tool calls and results are the pod's working, not the
    conversation the contact had, and may name things that were never theirs.
    A member's private note, and the answer it got, were never the contact's
    either, so neither is exported.
    """
    conversations = list(
        await uow.session.scalars(
            select(ConversationModel)
            .where(*_theirs(pod_id, contact_id))
            .order_by(ConversationModel.created_at)
            .limit(MAX_EXPORTED_CONVERSATIONS)
        )
    )
    return [
        ExportedConversation(
            id=conversation.id,
            title=conversation.title,
            created_at=conversation.created_at,
            messages=await _exported_messages(uow, conversation.id),
        )
        for conversation in conversations
    ]


async def _exported_messages(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> tuple[ExportedMessage, ...]:
    return await visible_messages(
        uow, conversation_id, after=-1, limit=MAX_EXPORTED_MESSAGES
    )


async def visible_messages(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, after: int, limit: int
) -> tuple[ExportedMessage, ...]:
    """What the person outside the pod saw of a conversation, after a point.

    Their words and the bot's answers, oldest first. Never a tool call, a
    member's private note, or what a note's run said back.
    """
    rows = await uow.session.execute(
        select(MessageModel, AgentRunModel.run_metadata)
        .outerjoin(AgentRunModel, AgentRunModel.id == MessageModel.agent_run_id)
        .where(
            MessageModel.conversation_id == conversation_id,
            MessageModel.sequence > after,
            MessageModel.role.in_(("user", "assistant")),
            MessageModel.text.is_not(None),
            MessageModel.tool_name.is_(None),
        )
        .order_by(MessageModel.sequence)
        .limit(limit)
    )
    return tuple(
        ExportedMessage(
            role=message.role,
            text=message.text or "",
            created_at=message.created_at,
            sequence=message.sequence,
        )
        for message, run_metadata in rows
        if not is_private_note(message.message_metadata)
        and not run_is_private(run_metadata)
    )


async def mark_conversation_contact(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, contact_id: UUID
) -> bool:
    """Make an anonymous outsiders' conversation this contact's, in place.

    Only a conversation already answering people outside the pod: a member's
    conversation never becomes anybody else's. Returns whether it changed.
    """
    conversation = await uow.session.get(ConversationModel, conversation_id)
    metadata = dict(conversation.conversation_metadata or {}) if conversation else {}
    if conversation is None or metadata.get(AUDIENCE_KEY) != OUTSIDERS:
        return False
    metadata.update({AUDIENCE_KEY: CONTACT, CONTACT_KEY: str(contact_id)})
    await uow.session.execute(
        update(ConversationModel)
        .where(ConversationModel.id == conversation_id)
        .values(conversation_metadata=metadata)
    )
    return True


async def append_follow_up(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    message: str,
    sent_by_user_id: UUID,
) -> None:
    """Write a member's follow-up into a contact's conversation.

    Belongs to no run, like a notification: it is so the agent, and the
    contact reading the thread, see what was sent and can answer it.
    """
    from app.modules.agent.domain.value_objects import MessageDraft
    from app.modules.agent.infrastructure.repositories.conversation_repository import (
        ConversationRepository,
    )

    await ConversationRepository(uow).append_message(
        conversation_id=conversation_id,
        agent_run_id=None,
        draft=MessageDraft.of_notification(
            message,
            metadata={"follow_up": True, "sent_by_user_id": str(sent_by_user_id)},
        ),
    )
