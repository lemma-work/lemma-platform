"""Bring an asking conversation back when the people it messaged answer.

``message_user`` does not pause, and should not: a person takes hours, and an
execution suspended for hours is one that cannot be told anything in the
meantime. The agent sends, finishes its turn, and the conversation goes quiet.

What was missing was the other half — nothing brought it back. This is that
half, and it is deliberately *not* a wait. The asking run declares nothing and
holds nothing open; the replies are simply input, and input starts a turn. Same
rule as a person typing, and the same one this module's own
``infrastructure.adapters.workflow_control`` already uses to hand a
system-triggered run its prompt.

Which is why an agent no longer has any reason to ``snooze`` after
``message_user``. Snooze is a timer for work with a real gap in it — a build, a
thing that needs to settle — and it stopped being this feature's business.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.wait import AgentWaitWakeReason
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.snooze_wake_service import SnoozeWakeService

logger = get_logger(__name__)

#: Marks the turn as system-authored on an otherwise ordinary user message, the
#: way a workflow-triggered run marks its prompt. The role has to be USER — it is
#: the turn the model answers — so the provenance lives here instead.
REPLY_SOURCE = "message_replies"

# Deliberately not the answers themselves. Batching them into this text needs a
# marker for which round has already been delivered, and gets it wrong the first
# time an agent asks a second question before the first is answered. The ids are
# in the model's own history next to the `message_user` calls that produced them,
# and `check_messages` already reads exactly the ones it is given.
REPLIES_ARRIVED = (
    "Everyone you messaged has now replied. Read them with `check_messages`, "
    "using the notification ids from your earlier `message_user` calls, then "
    "carry on with what you were doing."
)


class MessageReplyService:
    """Deliver "they have all answered" into the conversation that asked."""

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.conversations = ConversationRepository(uow)
        self.waits = AgentConversationWaitRepository(uow)

    async def deliver(self, *, conversation_id: UUID, pod_id: UUID) -> bool:
        """Start (or join) a turn carrying the news. False when there is none.

        Returns False for a conversation that has gone — the answers are still
        recorded on their rows, and a deleted conversation is not an error worth
        raising at somebody who just answered a question.
        """
        conversation = await self.conversations.get_conversation(
            conversation_id, include_runs=False
        )
        if conversation is None:
            return False

        token = set_current_context(
            await self._context_for(conversation.user_id, pod_id)
        )
        try:
            wait = await self.waits.find_active_for_conversation(conversation_id)
            if wait is not None:
                # Asleep for its own reasons. Resolving the pause it is holding
                # is the way back in: a new message would supersede that pause
                # while leaving its wait row armed to fire a second time later.
                return await SnoozeWakeService(self.uow).wake(
                    wait=wait, reason=AgentWaitWakeReason.ANSWERED
                )

            result = await self._conversation_service().turns.start(
                conversation,
                user_id=conversation.user_id,
                pod_id=pod_id,
                content=REPLIES_ARRIVED,
                agent_name=None,
                message_metadata={"source": REPLY_SOURCE},
            )
        finally:
            reset_current_context(token)

        logger.debug(
            "agent.message_replies.delivered",
            conversation_id=str(conversation_id),
            started_new_run=result.started_new_run,
        )
        # True either way: joining a live run is delivery too — that run's next
        # history rebuild reads the message, and starting a second run alongside
        # it is exactly what `turns.start` exists to prevent.
        return True

    # -- internals ---------------------------------------------------------------

    async def _context_for(self, user_id: UUID, pod_id: UUID) -> Context:
        return await create_authorization_data_service(self.uow).build_user_context(
            user_id=user_id,
            pod_id=pod_id,
        )

    def _conversation_service(self):
        from app.modules.usage.contracts.execution import build_usage_service
        from app.core.authorization.factory import create_authorization_data_service
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.conversation_service import ConversationService

        return ConversationService(
            uow=self.uow,
            conversation_repository=self.conversations,
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_data_service(self.uow),
            usage_service=build_usage_service(self.uow),
        )
