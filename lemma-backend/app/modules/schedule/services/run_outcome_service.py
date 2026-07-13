"""Apply target outcomes to the schedule ledger and failure circuit breaker."""

from __future__ import annotations

from datetime import datetime

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleRunStatus,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ScheduleRunOutcomeService:
    """Own schedule-run terminal transitions and breaker accounting."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.run_repository = ScheduleRunRepository(uow)
        self.schedule_repository = ScheduleRepository(uow=uow)

    async def record_target_outcome(
        self,
        *,
        target_kind: str,
        target_run_id: str,
        status: ScheduleRunStatus,
        completed_at: datetime | None,
        error_type: str | None = None,
    ) -> bool:
        """Record a target outcome once and update its schedule's streak."""
        schedule_run = await self.run_repository.transition_target_outcome(
            target_kind=target_kind,
            target_run_id=target_run_id,
            status=status,
            completed_at=completed_at,
            error_type=error_type,
        )
        if schedule_run is None:
            return False

        schedule = await self.schedule_repository.get_for_update(
            schedule_run.schedule_id
        )
        if schedule is None:
            raise LookupError(
                f"Schedule {schedule_run.schedule_id} disappeared during outcome update"
            )

        await self._apply_breaker(
            schedule,
            failed=status == ScheduleRunStatus.TARGET_FAILED,
        )
        return True

    async def record_dispatch_dead_letter(self, schedule: ScheduleEntity) -> None:
        """Count a delivery failure in the transaction that first dead-lettered it."""
        locked = await self.schedule_repository.get_for_update(schedule.id)
        if locked is None:
            raise LookupError(f"Schedule {schedule.id} disappeared during dispatch")
        await self._apply_breaker(locked, failed=True)

    async def reconcile_tripped_schedules(self) -> int:
        """Deactivate backfilled schedules already beyond the configured threshold."""
        threshold = schedule_settings.schedule_max_consecutive_failures
        if threshold <= 0:
            return 0

        schedules = await self.schedule_repository.lock_breaker_candidates(threshold)
        deactivated = 0
        for schedule in schedules:
            if await self._deactivate(schedule, schedule.consecutive_failures):
                deactivated += 1
        return deactivated

    async def _apply_breaker(
        self,
        schedule: ScheduleEntity,
        *,
        failed: bool,
    ) -> None:
        if not failed:
            await self.schedule_repository.reset_consecutive_failures(schedule.id)
            return

        count = await self.schedule_repository.increment_consecutive_failures(
            schedule.id
        )
        threshold = schedule_settings.schedule_max_consecutive_failures
        if threshold <= 0 or count < threshold:
            return
        await self._deactivate(schedule, count)

    async def _deactivate(self, schedule: ScheduleEntity, count: int) -> bool:
        if not await self.schedule_repository.deactivate_if_active(schedule.id):
            return False

        self.uow.collect_events(
            [
                ScheduleDeactivated(
                    schedule_id=schedule.id,
                    user_id=schedule.user_id,
                    schedule_type=schedule.schedule_type,
                    consecutive_failures=count,
                )
            ]
        )
        logger.warning(
            "Circuit breaker deactivated schedule",
            schedule_id=str(schedule.id),
            consecutive_failures=count,
        )
        return True
