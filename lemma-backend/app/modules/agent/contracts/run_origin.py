"""Whether a run's conversation was started by its person, in Lemma itself.

The workspace module asks this before letting a run onto a person's own
machine. A person's default machine holds their logins and their files, so a
run reaches it only when that person asked for the run in Lemma -- never one
started by a message on a channel, a contact, a schedule's task, or anybody
else.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.run_origin import started_in_lemma
from app.modules.agent.infrastructure.db.conversations import Conversations


async def conversation_started_in_lemma(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, user_id: UUID
) -> bool:
    conversation = await Conversations(uow).get_conversation(conversation_id)
    return conversation is not None and started_in_lemma(conversation, user_id)
