"""Persistence operations for durable schedule-run dispatch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.domain.schedule import (
    ScheduleRunEntity,
    ScheduleRunStatus,
)
from app.modules.schedule.infrastructure.models.run import ScheduleRun


class ScheduleRunRepository:
    MAX_ATTEMPTS = 10
    ABANDON_AFTER = timedelta(seconds=60)

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

    async def claim(
        self,
        *,
        schedule_id: UUID,
        user_id: UUID,
        source_event_id: str,
        target_kind: str,
        payload: dict,
        metadata: dict | None,
        llm_output: dict | None,
        source_occurred_at: datetime | None = None,
    ) -> ScheduleRunEntity | None:
        now = datetime.now(timezone.utc)
        created_id = await self.session.scalar(
            insert(ScheduleRun)
            .values(
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id=source_event_id,
                status=ScheduleRunStatus.PROCESSING.value,
                attempts=1,
                target_kind=target_kind,
                target_run_id=str(uuid7()),
                payload=payload,
                fire_metadata=metadata or {},
                llm_output=llm_output or {},
                source_occurred_at=source_occurred_at,
                started_at=now,
            )
            .on_conflict_do_nothing(
                constraint="uq_schedule_run_source_event"
            )
            .returning(ScheduleRun.id)
        )
        if created_id is not None:
            model = await self.session.get(ScheduleRun, created_id)
            assert model is not None
            return model.to_entity()

        model = await self.session.scalar(
            select(ScheduleRun)
            .where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.source_event_id == source_event_id,
            )
            .with_for_update()
        )
        if model is None:
            return None
        if model.status in {
            ScheduleRunStatus.DISPATCHED.value,
            ScheduleRunStatus.COMPLETED.value,
            ScheduleRunStatus.TARGET_FAILED.value,
            ScheduleRunStatus.CANCELLED.value,
            ScheduleRunStatus.FILTERED.value,
            ScheduleRunStatus.DEAD_LETTERED.value,
        }:
            return None
        if (
            model.status == ScheduleRunStatus.PROCESSING.value
            and model.started_at is not None
            and model.started_at > now - self.ABANDON_AFTER
        ):
            return None
        if model.attempts >= self.MAX_ATTEMPTS:
            model.status = ScheduleRunStatus.DEAD_LETTERED.value
            model.completed_at = now
            await self.session.flush()
            return model.to_entity()

        model.status = ScheduleRunStatus.PROCESSING.value
        model.attempts += 1
        model.started_at = now
        model.completed_at = None
        model.error_type = None
        model.error_code = None
        await self.session.flush()
        return model.to_entity()

    async def mark_dispatched(self, run_id: UUID) -> bool:
        """Mark launch complete unless a synchronous target outcome won the race."""
        changed = await self.session.scalar(
            update(ScheduleRun)
            .where(
                ScheduleRun.id == run_id,
                ScheduleRun.status == ScheduleRunStatus.PROCESSING.value,
            )
            .values(
                status=ScheduleRunStatus.DISPATCHED.value,
                error_type=None,
                error_code=None,
                completed_at=None,
            )
            .returning(ScheduleRun.id)
        )
        return changed is not None

    async def mark_failed(self, run_id: UUID, exc: Exception) -> ScheduleRunStatus:
        model = await self.session.get(ScheduleRun, run_id, with_for_update=True)
        if model is None:
            raise LookupError(f"Schedule run {run_id} no longer exists")
        if model.status in {
            ScheduleRunStatus.COMPLETED.value,
            ScheduleRunStatus.TARGET_FAILED.value,
            ScheduleRunStatus.CANCELLED.value,
            ScheduleRunStatus.DEAD_LETTERED.value,
        }:
            return ScheduleRunStatus(model.status)
        if model.status != ScheduleRunStatus.PROCESSING.value:
            raise RuntimeError(
                f"Cannot fail schedule run {run_id} from {model.status}"
            )
        status = (
            ScheduleRunStatus.DEAD_LETTERED
            if model.attempts >= self.MAX_ATTEMPTS
            else ScheduleRunStatus.FAILED
        )
        model.status = status.value
        model.error_type = type(exc).__name__
        model.error_code = getattr(exc, "code", None)
        model.completed_at = datetime.now(timezone.utc)
        await self.session.flush()
        return status

    async def transition_target_outcome(
        self,
        *,
        target_kind: str,
        target_run_id: str,
        status: ScheduleRunStatus,
        completed_at: datetime | None,
        error_type: str | None = None,
    ) -> ScheduleRunEntity | None:
        """Apply one target outcome, using the state transition as its idempotency gate."""
        if status not in {
            ScheduleRunStatus.COMPLETED,
            ScheduleRunStatus.TARGET_FAILED,
            ScheduleRunStatus.CANCELLED,
        }:
            raise ValueError(f"Invalid target outcome status: {status.value}")

        model = await self.session.scalar(
            select(ScheduleRun)
            .where(
                ScheduleRun.target_kind == target_kind,
                ScheduleRun.target_run_id == target_run_id,
            )
            .with_for_update()
        )
        if model is None or model.status not in {
            ScheduleRunStatus.PROCESSING.value,
            ScheduleRunStatus.DISPATCHED.value,
        }:
            return None

        model.status = status.value
        model.error_type = error_type
        model.error_code = None
        model.completed_at = completed_at or datetime.now(timezone.utc)
        await self.session.flush()
        return model.to_entity()

    async def list_for_schedule(
        self, schedule_id: UUID, *, limit: int = 100
    ) -> list[ScheduleRunEntity]:
        rows = await self.session.scalars(
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule_id)
            .order_by(ScheduleRun.created_at.desc(), ScheduleRun.id.desc())
            .limit(limit)
        )
        return [row.to_entity() for row in rows.all()]
