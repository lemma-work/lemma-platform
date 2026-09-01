"""Wake a snoozed conversation: resolve its wait and resume the agent.

Every wake — the scheduler timer, or the sweep healing a lost timer event —
funnels through :meth:`SnoozeWakeService.wake`, which claims the wait under a row
lock and then reuses the same pause/resume primitive the approvals path uses. The
lock is what makes a duplicate wake a no-op rather than a second run.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.pausing_tools import SNOOZE_TOOL_NAME
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitWakeReason,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.tools.snooze.models import (
    build_snooze_result,
    elapsed_seconds,
)

logger = get_logger(__name__)


class SnoozeWakeService:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.waits = AgentConversationWaitRepository(uow)
        self.conversations = ConversationRepository(uow)

    async def wake(
        self,
        *,
        wait: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason = AgentWaitWakeReason.TIMER,
    ) -> bool:
        """Resolve the wait and start the resume run. Idempotent.

        Returns False when another wake already claimed it — the common case
        being the reconciliation sweep racing the primary timer.
        """
        claimed = await self.waits.claim(wait.id)
        if claimed is None:
            logger.debug("agent.snooze.wake_already_claimed", wait_id=str(wait.id))
            return False

        conversation = await self.conversations.get_conversation(
            claimed.conversation_id, include_runs=False
        )
        if conversation is None:
            claimed.cancel()
            await self.waits.update(claimed)
            await self.uow.commit()
            return False

        claimed.complete(reason)
        await self.waits.update(claimed)

        # A wake has no sender, so it acts as whoever set the snooze -- which
        # is the trigger of the run that called the tool, recorded at that
        # moment. Resolving it from the conversation instead would mean the
        # authority a sleeping run wakes with could change when somebody joins
        # or leaves. The owner remains the answer for runs predating the column.
        paused_run = await self.conversations.get_agent_run(claimed.agent_run_id)
        acting_user_id = (
            paused_run.triggered_by_user_id
            if paused_run is not None and paused_run.triggered_by_user_id is not None
            else conversation.user_id
        )

        ctx = await self._context_for(acting_user_id, claimed.pod_id)
        token = set_current_context(ctx)
        try:
            service = self._conversation_service()
            await service.pauses.append_pause_tool_return(
                conversation=conversation,
                paused_run_id=claimed.agent_run_id,
                tool_call_id=claimed.tool_call_id,
                tool_name=SNOOZE_TOOL_NAME,
                tool_result=self._tool_result(claimed, reason),
            )
            await service.pauses.start_resume_run_if_ready(
                conversation=conversation,
                paused_run_id=claimed.agent_run_id,
                resumed_tool_call_id=claimed.tool_call_id,
                user_id=acting_user_id,
                pod_id=claimed.pod_id,
                agent_name=None,
                source="snooze_resume",
            )
        finally:
            reset_current_context(token)

        logger.debug(
            "agent.snooze.woke",
            conversation_id=str(claimed.conversation_id),
            woke_because=reason.value,
        )
        return True

    # -- internals ---------------------------------------------------------------

    def _tool_result(
        self,
        wait: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason,
    ) -> dict:
        spec = wait.spec or {}
        return build_snooze_result(
            woke_because=reason.value,
            slept_seconds=elapsed_seconds(spec.get("started_at")),
            note_to_self=spec.get("note_to_self"),
        )

    async def _context_for(self, user_id: UUID, pod_id: UUID) -> Context:
        return await create_authorization_data_service(self.uow).build_user_context(
            user_id=user_id,
            pod_id=pod_id,
        )

    def _conversation_service(self):
        from app.composition.agent_usage import build_usage_service
        from app.composition.authorization import create_authorization_service
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.conversation_service import ConversationService

        return ConversationService(
            uow=self.uow,
            conversation_repository=self.conversations,
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_service(self.uow),
            usage_service=build_usage_service(self.uow),
        )
