"""Whether a run is its person's own, asked for in Lemma itself.

The workspace module asks this before letting a run onto a person's own
machine. A person's default machine holds their logins and their files, so a
run reaches it only when it is that person's ``CHAT`` turn (or a sub-agent of
one) -- never a channel's line, a platform's message, a contact, a schedule, a
workflow or anybody else. See ``domain/started_in_lemma``.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.services.run_origin import run_started_in_lemma


async def conversation_started_in_lemma(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, user_id: UUID
) -> bool:
    """For the run a request on this conversation's token comes from."""
    conversation = await Conversations(uow).get_conversation(conversation_id)
    return conversation is not None and await run_started_in_lemma(
        uow, conversation, user_id
    )
