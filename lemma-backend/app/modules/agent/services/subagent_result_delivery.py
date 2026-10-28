"""Delivering a finished sub-agent's result to a parent that did not wait for it.

Runs when any run finishes. A child finishing may owe its parent a result; a
parent finishing may have left results uncollected while it was going. Both are
``settle``. See ``domain/subagent_results`` for when a result is owed, and the
claim that makes it arrive once.

A result is news, not an answer: it never overrules a question the parent's
owner has not answered (``supersede_pauses=False`` holds it behind the pause),
and it never wakes a parent asleep on ``wait_for`` -- if the wait is on this
child the wait carries the result, and if it is on something else the run that
wait resumes collects it when it finishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import AgentRun, Conversation
from app.modules.agent.domain.subagent_results import (
    DELIVERED_STATUSES,
    result_message,
    result_metadata,
)
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    ConversationStatus,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.application.turns.admit import AdmitMessage

logger = get_logger(__name__)

#: How many of a parent's children are looked at for owed results. A parent
#: spawns a handful; anything past this is collected on a later finish.
CHILDREN_WINDOW = 50


@dataclass(frozen=True, slots=True)
class _Owed:
    content: str
    metadata: dict[str, object]


async def mark_collected(uow: SqlAlchemyUnitOfWork, run_id: UUID) -> bool:
    """Record that a child run's result reached its parent -- by delivery, an
    await, a status check or a wait. True if first."""
    return await Runs(uow).claim_delivered_to_parent(run_id)


class SubagentResultDelivery:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def settle(self, *, conversation_id: UUID) -> int:
        """After a run finishes in this conversation, deliver what is owed.

        As a child, its parent may be owed this run's result; as a parent, it
        may have children whose results it left uncollected.
        """
        async with self.uow_factory() as uow:
            conversation = await Conversations(uow).get_conversation(conversation_id)
        if conversation is None:
            return 0
        delivered = 0
        if conversation.parent_id is not None and conversation.reports_to_parent:
            delivered += await self.deliver_owed(parent_id=conversation.parent_id)
        if conversation.delegations_out:
            delivered += await self.deliver_owed(parent_id=conversation.id)
        return delivered

    async def deliver_owed(self, *, parent_id: UUID) -> int:
        """Deliver every finished child result this parent has not collected."""
        async with self.uow_factory() as uow:
            conversations = Conversations(uow)
            parent = await conversations.get_conversation(parent_id, include_runs=True)
            if parent is None or parent.status is ConversationStatus.STOPPED:
                return 0
            if await Pauses(uow).pending(parent_id, kinds=(PauseKind.WAIT,)):
                return 0
            owed = await self._claim_owed(uow, parent)
            if not owed:
                return 0
            token = set_current_context(
                await create_authorization_data_service(uow).build_user_context(
                    user_id=parent.user_id, pod_id=parent.pod_id
                )
            )
            try:
                admit = AdmitMessage.build(uow)
                for result in owed:
                    await admit.admit(
                        parent,
                        user_id=parent.user_id,
                        content=result.content,
                        agent_name=None,
                        message_metadata=result.metadata,
                        # Continues the run it answers.
                        kind=None,
                        supersede_pauses=False,
                    )
            finally:
                reset_current_context(token)
            await uow.commit()
        logger.info(
            "agent.subagent_result.delivered",
            conversation_id=str(parent_id),
            results=len(owed),
        )
        return len(owed)

    async def _claim_owed(
        self, uow: SqlAlchemyUnitOfWork, parent: Conversation
    ) -> list[_Owed]:
        """Claim each finished, uncollected child run, and say what each reads."""
        parent_latest = parent.agent_runs[-1] if parent.agent_runs else None
        busy_run = (
            str(parent_latest.id)
            if parent_latest is not None
            and parent_latest.status in ACTIVE_AGENT_RUN_STATUSES
            else None
        )
        children = await Conversations(uow).list_child_conversations(
            parent_id=parent.id, user_id=parent.user_id, limit=CHILDREN_WINDOW
        )
        agents = await AgentRepository(uow).get_many(
            [child.agent_id for child in children]
        )
        owed: list[_Owed] = []
        for child in children:
            latest = child.agent_runs[-1] if child.agent_runs else None
            if not _finished_and_owed(child, latest, busy_run=busy_run):
                continue
            assert latest is not None
            if not await mark_collected(uow, latest.id):
                continue
            agent = agents.get(child.agent_id) if child.agent_id else None
            owed.append(
                _Owed(
                    content=result_message(
                        agent_name=agent.name if agent else DEFAULT_POD_AGENT_NAME,
                        child_conversation_id=child.id,
                        status=latest.status,
                        output=latest.output_data,
                        error=latest.error,
                    ),
                    metadata=result_metadata(
                        child_conversation_id=child.id, child_run_id=latest.id
                    ),
                )
            )
        return owed


def _finished_and_owed(
    child: Conversation, latest: AgentRun | None, *, busy_run: str | None
) -> bool:
    """Whether this child's latest run is a result its parent has yet to hear.

    Not while the parent run that gave it the work is still going: that run
    may await it, and its own finish delivers what it did not. Not while the
    child waits on a question either: that is not a result, and the parent
    reads it from the child's status when it looks.
    """
    if not child.reports_to_parent or latest is None:
        return False
    if latest.status not in DELIVERED_STATUSES:
        return False
    if child.status is ConversationStatus.WAITING:
        return False
    if latest.state.delivered_to_parent_at is not None:
        return False
    collector = str(child.parent_run_id) if child.parent_run_id else None
    return busy_run is None or collector != busy_run
