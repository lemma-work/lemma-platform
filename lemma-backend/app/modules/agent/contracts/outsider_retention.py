"""How long a conversation with nobody in particular is kept.

An anonymous visitor's conversation answers nobody the pod can name, write to,
or be asked by to export or forget it, so nothing would ever end it. It goes
once nothing has happened in it for long enough. A contact's conversation is
the pod's to keep or forget (``contact_conversations``) and is never touched
here: the audience is checked on the conversation itself, so one a visitor
went on to confirm their email in stays.

Deleting cascades to its runs, messages and routing links, as forgetting a
contact's does.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.outsiders import AUDIENCE_KEY, OUTSIDERS
from app.modules.agent.infrastructure.models.conversation import ConversationModel


async def delete_idle_outsider_conversations(
    uow: SqlAlchemyUnitOfWork, *, source: str, idle_since: datetime, batch: int
) -> int:
    """Delete up to ``batch`` anonymous conversations from ``source`` idle since then."""
    idle = (
        select(ConversationModel.id)
        .where(
            # Containment, so the metadata's GIN index answers it.
            ConversationModel.conversation_metadata.contains(
                {AUDIENCE_KEY: OUTSIDERS, "source": source}
            ),
            ConversationModel.last_activity_at < idle_since,
        )
        .limit(batch)
    )
    deleted = await uow.session.scalars(
        delete(ConversationModel)
        .where(ConversationModel.id.in_(idle))
        .returning(ConversationModel.id)
    )
    return len(deleted.all())
