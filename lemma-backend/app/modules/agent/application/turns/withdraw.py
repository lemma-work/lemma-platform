"""Taking back a message the agent has not seen yet."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.errors import ConversationStateError
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages
from app.modules.agent.services.conversation_access import (
    require_agent_action,
    validate_conversation_access,
)


class WithdrawQueuedMessage:
    """Deletes a queued message nobody is carrying yet."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.queued_messages = QueuedMessages(uow)

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> WithdrawQueuedMessage:
        return cls(uow)

    async def withdraw(
        self,
        *,
        conversation_id: UUID,
        message_id: UUID,
        user_id: UUID,
        pod_id: UUID,
    ) -> None:
        """Take back a message the agent has not seen yet. Commits.

        Only a queued message nobody is carrying: once a run has claimed it, or
        a steer is on its way to a host, it may already be in the agent's
        context, and deleting it would leave the agent answering something the
        person can no longer see. That is a conflict, not a missing message --
        the client drew it as queued a moment ago and should redraw it.
        """
        conversation = validate_conversation_access(
            await self.conversations.get_conversation(conversation_id),
            user_id=user_id,
            pod_id=pod_id,
        )
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )
        withdrawn = await self.queued_messages.withdraw_queued_user_message(
            conversation_id=conversation.id, message_id=message_id
        )
        if not withdrawn:
            raise ConversationStateError(
                "This message is no longer waiting: the agent already has it."
            )
        await self.uow.commit()
