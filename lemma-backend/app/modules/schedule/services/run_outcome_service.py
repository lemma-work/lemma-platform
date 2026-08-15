"""Apply target outcomes to the schedule ledger and failure circuit breaker."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.events.schedule import (
    ScheduleDeactivated,
    ScheduleRunCompleted,
)
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

        # In the same transaction as the outcome it reports, and after the
        # schedule is loaded so the pod is known without a second read.
        self.uow.collect_events(
            [
                ScheduleRunCompleted(
                    schedule_id=schedule.id,
                    schedule_type=schedule.schedule_type,
                    pod_id=schedule.pod_id,
                    status=status.value,
                )
            ]
        )

        await self._apply_breaker(schedule)
        return True

    async def record_dispatch_dead_letter(self, schedule: ScheduleEntity) -> None:
        """Count a delivery failure in the transaction that first dead-lettered it."""
        locked = await self.schedule_repository.get_for_update(schedule.id)
        if locked is None:
            raise LookupError(f"Schedule {schedule.id} disappeared during dispatch")
        await self._apply_breaker(locked)

    async def recompute_breaker(self, schedule_id: UUID) -> None:
        schedule = await self.schedule_repository.get_for_update(schedule_id)
        if schedule is None:
            raise LookupError(
                f"Schedule {schedule_id} disappeared during reconciliation"
            )
        await self._apply_breaker(schedule)

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
    ) -> None:
        """Advance the breaker for the *schedule*, whoever owned the run.

        Deliberate: on a shared RLS table any pod user's rows can drive runs, so
        a run owned by a row owner still counts toward the schedule owner's
        streak. The breaker protects the system from a persistently broken
        target, which is a property of the schedule and not of whoever happened
        to insert the row. The trade-off is that one user's bad data can pause a
        schedule for everyone; the owner is emailed on deactivation and can
        reactivate. See
        ``test_five_row_owner_workflow_failures_deactivate_schedule_owner_schedule``.
        """
        count = await self.run_repository.consecutive_terminal_failures(schedule.id)
        await self.schedule_repository.set_consecutive_failures(schedule.id, count)
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
            "schedule.breaker.tripped",
            schedule_id=str(schedule.id),
            consecutive_failures=count,
        )
        return True
