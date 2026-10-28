"""Bring an asking conversation back when the people it messaged answer.

``channel_post`` does not pause, and should not: a person takes hours, and an
execution suspended for hours is one that cannot be told anything in the
meantime. The agent sends, finishes its turn, and the conversation goes quiet.

What was missing was the other half — nothing brought it back. This is that
half, and it is deliberately *not* a wait. The asking run declares nothing and
holds nothing open; the replies are simply input, and input starts a turn. Same
rule as a person typing, and the same one this module's own
``application.automation.start`` already uses to hand a
system-triggered run its prompt.

Which is why an agent no longer has any reason to ``wait_for`` after
``channel_post``. Waiting is for work with a real gap in it — a build, a
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
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.wait_wake_service import AgentWaitService

logger = get_logger(__name__)

#: Marks the turn as system-authored on an otherwise ordinary user message, the
#: way a workflow-triggered run marks its prompt. The role has to be USER — it is
#: the turn the model answers — so the provenance lives here instead.
REPLY_SOURCE = "message_replies"

# Deliberately not the answers themselves. Batching them into this text needs a
# marker for which round has already been delivered, and gets it wrong the first
# time an agent asks a second question before the first is answered. The ids are
# in the model's own history next to the `channel_post` calls that produced them,
# and `channel_read(asked_by_me=True)` reads exactly this conversation's.
REPLIES_ARRIVED = (
    "Everyone you asked has now answered, or can no longer. Read what they said "
    "with `channel_read(asked_by_me=True)`, then carry on with what you were "
    "doing."
)


def replies_client_id(batch_id: str | None) -> str | None:
    """The news of one batch, named by it: delivered twice, it is one message."""
    return f"replies:{batch_id}" if batch_id else None


class MessageReplyService:
    """Deliver "they have all answered" into the conversation that asked."""

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.conversations = Conversations(uow)
        self.messages = Messages(uow)
        self.pauses = Pauses(uow)

    async def deliver(
        self, *, conversation_id: UUID, pod_id: UUID, batch_id: str | None = None
    ) -> bool:
        """Start (or join) a turn carrying the news. False when there is none.

        Returns False for a conversation that has gone — the answers are still
        recorded on their rows, and a deleted conversation is not an error worth
        raising at somebody who just answered a question — and for a batch whose
        news this conversation already has: a batch wakes it once.
        """
        conversation = await self.conversations.get_conversation(
            conversation_id, include_runs=False
        )
        if conversation is None:
            return False
        client_id = replies_client_id(batch_id)
        if client_id is not None and (
            await self.messages.find_user_message_by_client_id(
                conversation_id=conversation_id, client_message_id=client_id
            )
        ):
            return False

        token = set_current_context(
            await self._context_for(conversation.user_id, pod_id)
        )
        try:
            waits = await self.pauses.pending(conversation_id, kinds=(PauseKind.WAIT,))
            if waits:
                # Asleep for its own reasons. Resolving the pause it is holding
                # is the way back in: a new message would supersede that pause
                # while leaving its wait row armed to fire a second time later.
                return await AgentWaitService(self.uow).wake(
                    wait=waits[0], reason=AgentWaitWakeReason.ANSWERED
                )

            # Never past a question the owner has not answered: this is news,
            # not their answer, and starting a turn would deny the question on
            # their behalf. It waits behind the pause and the resume carries it.
            # Lazy, as the service it replaced was: the use case loads the
            # pause use cases and, through them, the tool models.
            from app.modules.agent.application.turns.admit import AdmitMessage

            result = await AdmitMessage.build(self.uow).admit(
                conversation,
                user_id=conversation.user_id,
                content=REPLIES_ARRIVED,
                agent_name=None,
                message_metadata={"source": REPLY_SOURCE},
                client_message_id=client_id,
                supersede_pauses=False,
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
        # it is exactly what `AdmitMessage` exists to prevent.
        return True

    # -- internals ---------------------------------------------------------------

    async def _context_for(self, user_id: UUID, pod_id: UUID) -> Context:
        return await create_authorization_data_service(self.uow).build_user_context(
            user_id=user_id,
            pod_id=pod_id,
        )
