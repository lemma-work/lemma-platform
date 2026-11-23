"""Whom a conversation answers, for a module that decides on it: machines.

Every run a member owns may use that member's own machine, whatever started
it -- chat, a schedule, a workflow, a channel message, a call, a sub-agent.
A run answering somebody outside the pod -- a contact, or another pod asking
over a link -- may not: it is the pod's, acting toward them, and its toolset
already has no machine (``domain/outsiders``). The workspace module asks this
as its second line behind that toolset, from the conversation row.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.infrastructure.db.conversations import Conversations


def answers_member(conversation: Conversation | None) -> bool:
    """Whom the conversation answers decides it; how its run started does not."""
    return conversation is not None and not (
        Audience.of(conversation).answers_outsiders
    )


async def conversation_answers_member(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID
) -> bool:
    """True when the conversation exists and answers a member of its pod."""
    return answers_member(await Conversations(uow).get_conversation(conversation_id))


__all__ = ["answers_member", "conversation_answers_member"]
