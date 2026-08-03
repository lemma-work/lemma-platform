"""Self-heal snoozed conversations whose scheduler wake never arrived.

Mirrors ``RunResumeService.reconcile_stale_waits`` for TIME waits: there is no
external system to poll, a timer just has to elapse, so an overdue wait is fired
here.

Three things keep this a backstop rather than a second wake path:

* **A grace period.** ``RECONCILE_AFTER`` matches the workflow sweep's. Firing
  the moment ``scheduled_at`` passes would race the real timer on every snooze,
  and every healthy wait would log "lost timer" at WARNING.
* **A session per wait.** A wake that raises rolls its session back; sharing one
  across the batch would leave every later wait riding a broken transaction.
* **An attempt cap.** A wake that always raises would otherwise be retried every
  five minutes forever. Attempts are counted in a transaction of their own — the
  failing wake rolls its own back — and the wait is abandoned once the cap is hit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitWakeReason,
)
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.snooze_wake_service import SnoozeWakeService

logger = get_logger(__name__)

RECONCILE_BATCH = 100

# How far past due a wait must be before the sweep treats its timer as lost.
# The same value the workflow sweep uses, for the same reason.
RECONCILE_AFTER = timedelta(minutes=10)

# Wakes to attempt before giving up. Three sweeps is ~15 minutes of transient
# failure (a database blip, a restarting worker) before we call it permanent.
MAX_WAKE_ATTEMPTS = 3


class SnoozeReconcileService:
    def __init__(self) -> None:
        # No shared unit of work on purpose: every step below opens its own, so
        # one wait's rollback cannot poison the rest of the batch.
        pass

    async def reconcile_due_waits(self) -> int:
        """Fire every ACTIVE wait whose timer looks lost. Returns waits woken."""
        cutoff = datetime.now(timezone.utc) - RECONCILE_AFTER
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            due = await AgentConversationWaitRepository(uow).list_active_due(
                due_before=cutoff,
                limit=RECONCILE_BATCH,
            )
        woken = 0
        for wait in due:
            if await self._reconcile_one(wait):
                woken += 1
        return woken

    async def _reconcile_one(self, wait: AgentConversationWaitEntity) -> bool:
        attempt = await self._count_attempt(wait)
        if attempt > MAX_WAKE_ATTEMPTS:
            await self._abandon(wait, attempt)
            return False
        try:
            async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
                woke = await SnoozeWakeService(uow).wake(
                    wait=wait, reason=AgentWaitWakeReason.TIMER
                )
        except Exception:
            logger.error(
                "agent.snooze.reconcile_failed",
                wait_id=str(wait.id),
                attempt=attempt,
                exc_info=True,
            )
            return False
        if woke:
            logger.warning(
                "agent.snooze.reconcile_fired_lost_timer",
                conversation_id=str(wait.conversation_id),
                wait_id=str(wait.id),
            )
        return woke

    async def _count_attempt(self, wait: AgentConversationWaitEntity) -> int:
        """Bump and commit the counter before the attempt it counts."""
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            attempt = await AgentConversationWaitRepository(uow).record_wake_attempt(
                wait.id
            )
            await uow.commit()
        return attempt

    async def _abandon(self, wait: AgentConversationWaitEntity, attempt: int) -> None:
        """Stop retrying a wait whose wake never succeeds.

        The conversation is left WAITING rather than resumed: we never managed to
        build a tool return, so there is nothing truthful to hand the model. The
        error log is the operator's signal — a stuck conversation is bad, but a
        hot retry loop that hides it is worse.
        """
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            repo = AgentConversationWaitRepository(uow)
            claimed = await repo.claim(wait.id)
            if claimed is None:
                return
            claimed.abandon(f"wake failed {MAX_WAKE_ATTEMPTS} times")
            await repo.update(claimed)
            await uow.commit()
        logger.error(
            "agent.snooze.reconcile_abandoned",
            conversation_id=str(wait.conversation_id),
            wait_id=str(wait.id),
            attempt=attempt,
        )
