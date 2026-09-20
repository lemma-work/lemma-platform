"""Resolving a suspended conversation's wait, and resuming the agent.

Two entry points, and the difference between them is who already knows the
answer. :meth:`AgentWaitService.wake` is told the reason and always wakes —
that is the path for an answer landing or a wait being cancelled.
:meth:`AgentWaitService.resolve` is handed a wait whose timer fired and has to
work out what that means, which for anything but a plain timer is a question
about something else: has the process exited, has the child run finished, or is
it simply not time to give up yet.

Both claim the wait under a row lock before touching it, and that lock is what
makes a duplicate resolution a no-op rather than a second run. The target is
read *before* the claim on purpose: probing a sandbox or reading a child run
opens its own sessions, and doing that while holding a row lock in another is
how a five-second check turns into a lock held for a five-second check.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.pausing_tools import WAIT_TOOL_NAME
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitWakeReason,
)
from app.modules.agent.domain.wait_decision import WakeNow, decide_wait
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.wait_targets import read_target
from app.modules.agent.tools.waiting.models import (
    build_wait_result,
    elapsed_seconds,
    next_poll_delay,
)

logger = get_logger(__name__)


class AgentWaitService:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.waits = AgentConversationWaitRepository(uow)
        self.conversations = ConversationRepository(uow)

    async def resolve(self, *, wait: AgentConversationWaitEntity) -> bool:
        """Decide what a fired wait means, then wake or re-arm. Idempotent.

        Returns True only when the conversation was actually resumed. A re-armed
        wait returns False, which is not a failure — it is this method saying
        "not yet", and it is the normal answer for most checks of a long wait.
        """
        outcome = await self._read_target(wait)

        claimed = await self.waits.claim(wait.id)
        if claimed is None:
            logger.debug("agent.wait.already_claimed", wait_id=str(wait.id))
            return False

        now = datetime.now(timezone.utc)
        decision = decide_wait(
            claimed,
            target_reason=outcome.reason,
            now=now,
            poll_delay_seconds=next_poll_delay(
                int(claimed.spec.get("poll_attempt", 0))
            ),
        )
        if isinstance(decision, WakeNow):
            return await self._wake_claimed(
                claimed, decision.reason, exit_code=outcome.exit_code
            )

        claimed.rearm(decision.next_at)
        await self.waits.update(claimed)
        await self.uow.commit()
        return False

    async def wake(
        self,
        *,
        wait: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason = AgentWaitWakeReason.TIMER,
    ) -> bool:
        """Resolve the wait for a reason the caller already knows. Idempotent.

        Returns False when another resolution already claimed it — the common
        case being the reconciliation sweep racing the primary timer.
        """
        claimed = await self.waits.claim(wait.id)
        if claimed is None:
            logger.debug("agent.wait.already_claimed", wait_id=str(wait.id))
            return False
        return await self._wake_claimed(claimed, reason)

    # -- internals ---------------------------------------------------------------

    async def _read_target(self, wait: AgentConversationWaitEntity):
        spec = wait.spec or {}
        user_id = spec.get("user_id")
        if not user_id:
            conversation = await self.conversations.get_conversation(
                wait.conversation_id, include_runs=False
            )
            user_id = str(conversation.user_id) if conversation else None
        if not user_id:
            from app.modules.agent.services.wait_targets import TargetOutcome

            return TargetOutcome(reason=None)
        return await read_target(
            wait_type=wait.wait_type,
            target_ref=spec.get("target_ref"),
            user_id=UUID(str(user_id)),
            pod_id=wait.pod_id,
        )

    async def _wake_claimed(
        self,
        claimed: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason,
        *,
        exit_code: int | None = None,
    ) -> bool:
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

        ctx = await self._context_for(conversation.user_id, claimed.pod_id)
        token = set_current_context(ctx)
        try:
            service = self._conversation_service()
            await service.pauses.append_pause_tool_return(
                conversation=conversation,
                paused_run_id=claimed.agent_run_id,
                tool_call_id=claimed.tool_call_id,
                tool_name=WAIT_TOOL_NAME,
                tool_result=self._tool_result(claimed, reason, exit_code),
            )
            await service.pauses.start_resume_run_if_ready(
                conversation=conversation,
                paused_run_id=claimed.agent_run_id,
                resumed_tool_call_id=claimed.tool_call_id,
                user_id=conversation.user_id,
                pod_id=claimed.pod_id,
                agent_name=None,
                source="wait_resume",
            )
        finally:
            reset_current_context(token)

        logger.debug(
            "agent.wait.woke",
            conversation_id=str(claimed.conversation_id),
            wait_type=claimed.wait_type.value,
            woke_because=reason.value,
        )
        return True

    def _tool_result(
        self,
        wait: AgentConversationWaitEntity,
        reason: AgentWaitWakeReason,
        exit_code: int | None,
    ) -> dict:
        spec = wait.spec or {}
        return build_wait_result(
            wait_type=wait.wait_type,
            reason=reason,
            waited_seconds=elapsed_seconds(spec.get("started_at")),
            note_to_self=spec.get("note_to_self"),
            exit_code=exit_code,
        )

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
