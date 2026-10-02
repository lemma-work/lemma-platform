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
from app.modules.agent.domain.outsiders import (
    AUDIENCE_KEY,
    OUTSIDERS,
    answers_outsiders,
)
from app.modules.agent_surfaces.contracts.conversations import (
    conversation_answers_outsiders,
)

logger = get_logger(__name__)


#: Whether routing's link names a conversation as the strangers' thread.
LinkedToOutsiders = Callable[[IUnitOfWork, UUID], Awaitable[bool]]


async def with_effective_audience(
    uow: IUnitOfWork,
    conversation: Conversation,
    *,
    linked_to_outsiders: LinkedToOutsiders = conversation_answers_outsiders,
) -> Conversation:
    """``conversation``, marked as answering outsiders when either record says so.

    Only ever adds the mark, in memory: a conversation the link names as the
    strangers' thread is treated as theirs whatever its metadata says. The row
    is not repaired here -- the next stranger's message rebinds the link to a
    flagged conversation (``ConversationBinder``).
    """
    if answers_outsiders(conversation):
        return conversation
    if not await linked_to_outsiders(uow, conversation.id):
        return conversation
    logger.error(
        "agent.outsiders.unflagged_link.error",
        conversation_id=str(conversation.id),
    )
    metadata = (
        dict(conversation.metadata) if isinstance(conversation.metadata, dict) else {}
    )
    metadata[AUDIENCE_KEY] = OUTSIDERS
    return conversation.model_copy(update={"metadata": metadata})
