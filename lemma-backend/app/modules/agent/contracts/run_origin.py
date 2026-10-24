"""Whether a run's conversation was started by its person, in Lemma itself.

The workspace module asks this before letting a run onto a person's own
machine. A person's default machine holds their logins and their files, so a
run reaches it only when that person asked for the run in Lemma -- never one
started by a message on a channel, a contact, a schedule's task, or anybody
else.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.value_objects import ConversationType
from app.modules.agent.infrastructure.models.conversation import ConversationModel


async def conversation_started_in_lemma(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, user_id: UUID
) -> bool:
    row = (
        await uow.session.execute(
            select(
                ConversationModel.user_id,
                ConversationModel.channel_id,
                ConversationModel.contact_id,
                ConversationModel.conversation_type,
            ).where(ConversationModel.id == conversation_id)
        )
    ).one_or_none()
    if row is None:
        return False
    owner, channel_id, contact_id, conversation_type = row
    return (
        owner == user_id
        and channel_id is None
        and contact_id is None
        and conversation_type != ConversationType.TASK.value
    )
