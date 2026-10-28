"""A contact's conversations, for the module that exports and forgets contacts.

A contact's conversation names them in its ``contact_id`` column
(``domain/outsiders``). An export is what they saw of each, text only
(``infrastructure/db/contact_history``); forgetting deletes the conversations,
which cascades to their runs, messages, pauses and routing links --
notifications that pointed at one lose the pointer, which is why the caller
redacts them first.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.contact_history import (
    MAX_EXPORTED_CONVERSATIONS,
    ConversationCursor,
    ConversationsPage,
    ExportedConversation,
    ExportedMessage,
)
from app.modules.agent.infrastructure.db.channel_conversations import (
    ChannelConversations,
)


async def export_contact_conversations(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    contact_id: UUID,
    after: ConversationCursor | None = None,
    limit: int | None = None,
) -> ConversationsPage:
    """What was said with this contact, a page of conversations at a time.

    Tool calls, a member's private note and the answer it got were never the
    contact's, so none is exported.
    """
    from app.modules.agent.infrastructure.db.contact_history import ContactHistory

    return await ContactHistory(uow).export_page(
        pod_id=pod_id,
        contact_id=contact_id,
        after=after,
        limit=limit if limit is not None else MAX_EXPORTED_CONVERSATIONS,
    )


async def forget_contact_conversations(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> int:
    """Delete every conversation this pod had with this contact."""
    return await ChannelConversations(uow).forget_contact(
        pod_id=pod_id, contact_id=contact_id
    )


__all__ = [
    "ConversationCursor",
    "ConversationsPage",
    "ExportedConversation",
    "ExportedMessage",
    "export_contact_conversations",
    "forget_contact_conversations",
]
