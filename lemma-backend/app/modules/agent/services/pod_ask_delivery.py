"""Bringing another pod's answer back to the conversation that asked.

Delivery happens when a run finishes, in either pod. A run finishing in the pod
that was asked delivers its answer into the asking conversation; a run finishing
in the asking conversation collects any answer that arrived while it could not
take one. Both are ``settle``.

Delivery waits while the asking conversation is paused on a person. A message
arriving then would deny the approval or question they have not answered yet
(``TurnCoordinator.start`` supersedes them), and an answer from another pod is
not theirs to overrule. Whatever run ends that pause collects the answer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_asks import (
    ASKS_OUT_KEY,
    AskMode,
    PodAsk,
    answer_from,
    answer_message,
    answer_message_metadata,
    ask_of,
    unfinished_message,
)
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    AgentRunStatus,
    ConversationStatus,
    JsonObject,
)
from app.modules.agent.infrastructure.pod_ask_queries import PodAskQueries
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.pod.contracts.members import pod_name
from app.modules.usage.contracts.execution import build_usage_service

logger = get_logger(__name__)

#: How many of the other pod's latest messages are read to find its answer.
#: The answer is what its last run wrote after its last tool call, which is the
#: tail of the conversation by construction.
ANSWER_WINDOW = 200
#: A conversation in one of these is not done with the ask yet.
BUSY = frozenset(
    {
        ConversationStatus.RUNNING,
        ConversationStatus.STOP_REQUESTED,
        ConversationStatus.WAITING,
    }
)


def owed_to(ask: PodAsk | None, thread: Conversation, asking: Conversation) -> bool:
    """Whether this thread's answer is the asking conversation's to receive.

    The thread names the asking conversation; it must also be from that pod --
    and, asked as a person, theirs on both ends -- or it is not an answer this
    conversation is owed, whatever its metadata says.
    """
    if ask is None or ask.from_pod_id != asking.pod_id:
        return False
    if ask.mode is AskMode.AS_PERSON:
        return ask.for_user_id == asking.user_id == thread.user_id
    return True


class PodAskDelivery:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def settle(self, *, conversation_id: UUID) -> int:
        """After a run finishes in this conversation, deliver what is owed.

        It may be answering an ask, so its answer goes to the asker; it may have
        asked, so answers that arrived while it was paused come in now.
        """
        async with self.uow_factory() as uow:
            conversation = await ConversationRepository(uow).get_conversation(
                conversation_id
            )
        if conversation is None:
            return 0
        delivered = 0
        ask = ask_of(conversation)
        if ask is not None:
            delivered += await self.deliver_owed(
                asking_conversation_id=ask.from_conversation_id
            )
        metadata = (
            conversation.metadata if isinstance(conversation.metadata, dict) else {}
        )
        if metadata.get(ASKS_OUT_KEY) is True:
            delivered += await self.deliver_owed(asking_conversation_id=conversation.id)
        return delivered

    async def deliver_owed(self, *, asking_conversation_id: UUID) -> int:
        """Deliver every answer this conversation is owed and has not been given."""
        async with self.uow_factory() as uow:
            asking = await ConversationRepository(uow).get_conversation(
                asking_conversation_id, include_runs=True
            )
            if asking is None or asking.status is ConversationStatus.WAITING:
                return 0
            deliveries = await self._claim_owed(uow, asking)
            if not deliveries:
                return 0
            token = set_current_context(
                await create_authorization_data_service(uow).build_user_context(
                    user_id=asking.user_id, pod_id=asking.pod_id
                )
            )
            try:
                turns = ConversationService(
                    uow=uow,
                    conversation_repository=ConversationRepository(uow),
                    agent_repository=AgentRepository(uow),
                    authorization_service=create_authorization_data_service(uow),
                    usage_service=build_usage_service(uow),
                ).turns
                for content, metadata in deliveries:
                    await turns.start(
                        asking,
                        user_id=asking.user_id,
                        pod_id=asking.pod_id,
                        content=content,
                        agent_name=None,
                        message_metadata=metadata,
                    )
            finally:
                reset_current_context(token)
            await uow.commit()
        logger.info(
            "agent.pod_ask.delivered",
            conversation_id=str(asking_conversation_id),
            answers=len(deliveries),
        )
        return len(deliveries)

    async def _claim_owed(
        self, uow: SqlAlchemyUnitOfWork, asking: Conversation
    ) -> list[tuple[str, JsonObject]]:
        """Claim each settled, undelivered answer, and say what each delivery reads."""
        conversations = ConversationRepository(uow)
        queries = PodAskQueries(uow)
        deliveries: list[tuple[str, JsonObject]] = []
        for thread_id in await queries.list_threads(from_conversation_id=asking.id):
            thread = await conversations.get_conversation(thread_id, include_runs=True)
            if thread is None or thread.status in BUSY:
                continue
            ask = ask_of(thread)
            if not owed_to(ask, thread, asking):
                continue
            if ask is not None and ask.awaited_inline(datetime.now(timezone.utc)):
                continue
            latest = thread.agent_runs[-1] if thread.agent_runs else None
            if latest is None or latest.status in ACTIVE_AGENT_RUN_STATUSES:
                continue
            if (
                ask is None
                or ask.delivered_run_id == latest.id
                or not await queries.claim_delivery(
                    conversation_id=thread.id, run_id=latest.id
                )
            ):
                continue
            name = await pod_name(uow.session, thread.pod_id) or "The other pod"
            if latest.status is AgentRunStatus.COMPLETED:
                messages, _ = await conversations.list_messages(
                    conversation_id=thread.id, limit=ANSWER_WINDOW
                )
                content = answer_message(
                    pod_name=name, answer=answer_from(messages, run_id=latest.id)
                )
            else:
                content = unfinished_message(pod_name=name, reason=latest.error)
            deliveries.append(
                (
                    content,
                    answer_message_metadata(
                        to_pod_id=thread.pod_id,
                        to_pod_name=name,
                        ask_conversation_id=thread.id,
                        run_id=latest.id,
                    ),
                )
            )
        return deliveries
