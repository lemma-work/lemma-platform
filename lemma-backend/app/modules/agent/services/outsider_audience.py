"""Whether a run answers somebody outside the pod, asked so that it fails closed.

The fact is written twice, by the one path that creates such a conversation:
on the conversation (``metadata.audience``, see ``domain/outsiders``) and on
the routing link that points a group's strangers at it. Every consumer reads
the conversation, so that is where the answer has to be -- but the metadata is
the half a client can reach, and losing it would hand the next stranger the
owner's authority. So the link is asked too, every time, and either one saying
"outsiders" makes the run a stranger's.

Called where a run's conversation is loaded -- the runner, the MCP bridge, the
approval executor -- so everything downstream of those loads reads one answer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.domain.uow import IUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent_surfaces.contracts.conversations import (
    OutsideLink,
    conversation_outside_link,
)

logger = get_logger(__name__)


#: Whom routing's link says a conversation answers, if anybody outside the pod.
LinkedAudience = Callable[[IUnitOfWork, UUID], Awaitable[OutsideLink | None]]


async def with_effective_audience(
    uow: IUnitOfWork,
    conversation: Conversation,
    *,
    linked_audience: LinkedAudience = conversation_outside_link,
) -> Conversation:
    """``conversation``, marked as answering outsiders when either record says so.

    Only ever adds the mark, in memory: a conversation the link names as the
    strangers' thread is treated as theirs whatever its metadata says, and one
    the link names as a contact's chat as that contact's -- it keeps their own
    rows and their name, rather than becoming a group's anonymous strangers.
    The row is not repaired here -- the next stranger's message rebinds the
    link to a flagged conversation (``ConversationBinder``).
    """
    if Audience.of(conversation).answers_outsiders:
        return conversation
    link = await linked_audience(uow, conversation.id)
    if link is None:
        return conversation
    logger.error(
        "agent.outsiders.unflagged_link.error",
        conversation_id=str(conversation.id),
    )
    audience = (
        Audience.contact(link.contact_id)
        if link.contact_id is not None
        else Audience.outsiders()
    )
    metadata = (
        dict(conversation.metadata) if isinstance(conversation.metadata, dict) else {}
    )
    metadata.update(audience.to_metadata())
    return conversation.model_copy(update={"metadata": metadata})
