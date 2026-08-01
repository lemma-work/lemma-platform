"""Cross-module reconciliation for durable schedule target dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import or_, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)
from app.modules.workflow.infrastructure.models import WorkflowRunModel


@dataclass(frozen=True, slots=True)
class ScheduleRunRecoveryResult:
    redelivered: int = 0
    reconciled: int = 0
    dead_lettered: int = 0


class ScheduleRunRecoveryService:
    BATCH_SIZE = 100
    DISPATCH_RECONCILE_AFTER = timedelta(minutes=5)

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

    async def recover(self, *, limit: int = BATCH_SIZE) -> ScheduleRunRecoveryResult:
        now = datetime.now(timezone.utc)
        retry_cutoff = now - ScheduleRunRepository.ABANDON_AFTER
        dispatch_cutoff = now - self.DISPATCH_RECONCILE_AFTER
        rows = (
            await self.session.execute(
                select(ScheduleRun, Schedule)
                .join(Schedule, Schedule.id == ScheduleRun.schedule_id)
                .where(
                    Schedule.is_active.is_(True),
                    ScheduleRun.target_outcome.is_(None),
                    or_(
                        (ScheduleRun.status == ScheduleRunStatus.PROCESSING.value)
                        & or_(
                            ScheduleRun.started_at.is_(None),
                            ScheduleRun.started_at < retry_cutoff,
                        ),
                        (ScheduleRun.status == ScheduleRunStatus.FAILED.value)
                        & (ScheduleRun.updated_at < retry_cutoff),
                        (ScheduleRun.status == ScheduleRunStatus.DISPATCHED.value)
                        & (ScheduleRun.updated_at < dispatch_cutoff),
                    ),
                )
                .order_by(ScheduleRun.updated_at, ScheduleRun.id)
                .limit(max(1, min(limit, self.BATCH_SIZE)))
                .with_for_update(skip_locked=True, of=ScheduleRun)
            )
        ).all()

        redelivered = 0
        reconciled = 0
        dead_lettered = 0
        breaker_schedule_ids: set[UUID] = set()

        for run, schedule in rows:
            target_exists, outcome, completed_at = await self._resolve_target(run)
            if outcome is not None:
                run.status = ScheduleRunStatus.DISPATCHED.value
                run.target_outcome = outcome.value
                run.completed_at = completed_at or now
                run.error_type = (
                    f"{run.target_kind.title()}TargetFailed"
                    if outcome == ScheduleRunStatus.TARGET_FAILED
                    else None
                )
                run.error_code = None
                breaker_schedule_ids.add(schedule.id)
                reconciled += 1
                continue
            if target_exists:
                run.status = ScheduleRunStatus.DISPATCHED.value
                run.completed_at = None
                run.error_type = None
                run.error_code = None
                reconciled += 1
                continue

            if run.attempts >= ScheduleRunRepository.MAX_ATTEMPTS:
                run.status = ScheduleRunStatus.DEAD_LETTERED.value
                run.completed_at = now
                run.error_type = run.error_type or "ScheduleDispatchExhausted"
                breaker_schedule_ids.add(schedule.id)
                dead_lettered += 1
                continue

            if run.user_id is None:
                if schedule.schedule_type == ScheduleType.DATASTORE:
                    run.status = ScheduleRunStatus.DEAD_LETTERED.value
                    run.completed_at = now
                    run.error_type = "ScheduleRunOwnerMissing"
                    breaker_schedule_ids.add(schedule.id)
                    dead_lettered += 1
                    continue
                run.user_id = schedule.user_id
            if run.target_run_id is None:
                run.target_run_id = str(uuid7())

            run.status = ScheduleRunStatus.RECEIVED.value
            run.started_at = None
            run.completed_at = None
            run.error_type = None
            run.error_code = None
            self.uow.collect_events(
                [
                    ScheduleFired(
                        schedule_id=schedule.id,
                        user_id=run.user_id,
                        schedule_type=schedule.schedule_type,
                        pod_id=schedule.pod_id,
                        account_id=schedule.account_id,
                        payload=run.payload or {},
                        metadata=run.fire_metadata or {},
                        llm_output=run.llm_output or {},
                        scheduled_at=run.source_occurred_at,
                        source_event_id=run.source_event_id,
                        causation_id=run.id,
                    )
                ]
            )
            redelivered += 1

        await self.session.flush()
        outcome_service = ScheduleRunOutcomeService(self.uow)
        for schedule_id in breaker_schedule_ids:
            await outcome_service.recompute_breaker(schedule_id)

        return ScheduleRunRecoveryResult(
            redelivered=redelivered,
            reconciled=reconciled,
            dead_lettered=dead_lettered,
        )

    async def _resolve_target(
        self, run: ScheduleRun
    ) -> tuple[bool, ScheduleRunStatus | None, datetime | None]:
        try:
            target_id = UUID(str(run.target_run_id))
        except TypeError, ValueError:
            return False, None, None

        if run.target_kind == "WORKFLOW":
            target = await self.session.get(WorkflowRunModel, target_id)
            if target is None:
                return False, None, None
            return True, _target_outcome(target.status), target.completed_at
        if run.target_kind == "AGENT":
            target = await self.session.get(ConversationModel, target_id)
            if target is None:
                return False, None, None
            return True, _target_outcome(target.status), target.updated_at
        return False, None, None


def _target_outcome(status: str | None) -> ScheduleRunStatus | None:
    return {
        "COMPLETED": ScheduleRunStatus.COMPLETED,
        "FAILED": ScheduleRunStatus.TARGET_FAILED,
        "CANCELLED": ScheduleRunStatus.CANCELLED,
        "STOPPED": ScheduleRunStatus.CANCELLED,
    }.get(status or "")
