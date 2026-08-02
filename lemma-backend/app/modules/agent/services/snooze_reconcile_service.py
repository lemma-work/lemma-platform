"""Self-heal snoozed conversations whose scheduler wake never arrived.

Mirrors ``RunResumeService.reconcile_stale_waits`` for TIME waits: there is no
external system to poll, a timer just has to elapse, so a past-due wait is fired
here. A wait legitimately scheduled into the future is left alone, and waking is
idempotent, so racing the primary timer is harmless.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.domain.wait import AgentWaitWakeReason
from app.modules.agent.infrastructure.repositories import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.snooze_wake_service import SnoozeWakeService

logger = get_logger(__name__)

RECONCILE_BATCH = 100


class SnoozeReconcileService:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.waits = AgentConversationWaitRepository(uow)

    async def reconcile_due_waits(self) -> int:
        """Fire every ACTIVE wait already past its scheduled wake time."""
        now = datetime.now(timezone.utc)
        due = await self.waits.list_active_due(now=now, limit=RECONCILE_BATCH)
        woken = 0
        for wait in due:
            try:
                if await SnoozeWakeService(self.uow).wake(
                    wait=wait, reason=AgentWaitWakeReason.TIMER
                ):
                    woken += 1
                    logger.warning(
                        "agent.snooze.reconcile_fired_lost_timer",
                        conversation_id=str(wait.conversation_id),
                        wait_id=str(wait.id),
                    )
            except Exception:
                logger.error(
                    "agent.snooze.reconcile_failed",
                    wait_id=str(wait.id),
                    exc_info=True,
                )
        return woken
