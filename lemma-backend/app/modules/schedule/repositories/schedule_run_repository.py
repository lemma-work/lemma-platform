"""Persistence operations for durable schedule-run dispatch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

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
                source_event_id=source_event_id,
                status=ScheduleRunStatus.PROCESSING.value,
                attempts=1,
                target_kind=target_kind,
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
            ScheduleRunStatus.FILTERED.value,
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
            return None

        model.status = ScheduleRunStatus.PROCESSING.value
        model.attempts += 1
        model.started_at = now
        model.completed_at = None
        model.error_type = None
        model.error_code = None
        await self.session.flush()
        return model.to_entity()

    async def mark_dispatched(self, run_id: UUID, *, target_run_id: str | None) -> None:
        await self._mark(
            run_id,
            status=ScheduleRunStatus.DISPATCHED,
            target_run_id=target_run_id,
        )

    async def mark_failed(self, run_id: UUID, exc: Exception) -> ScheduleRunStatus:
        model = await self.session.get(ScheduleRun, run_id, with_for_update=True)
        if model is None:
            raise LookupError(f"Schedule run {run_id} no longer exists")
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

    async def consecutive_terminal_failures(self, schedule_id: UUID) -> int:
        """Count distinct dead-lettered runs since the latest dispatch."""
        statuses = (
            await self.session.scalars(
                select(ScheduleRun.status)
                .where(
                    ScheduleRun.schedule_id == schedule_id,
                    ScheduleRun.status.in_(
                        (
                            ScheduleRunStatus.DISPATCHED.value,
                            ScheduleRunStatus.DEAD_LETTERED.value,
                        )
                    ),
                )
                .order_by(ScheduleRun.created_at.desc(), ScheduleRun.id.desc())
            )
        ).all()
        failures = 0
        for status in statuses:
            if status == ScheduleRunStatus.DISPATCHED.value:
                break
            failures += 1
        return failures

    async def _mark(
        self,
        run_id: UUID,
        *,
        status: ScheduleRunStatus,
        target_run_id: str | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
    ) -> None:
        await self.session.execute(
            update(ScheduleRun)
            .where(ScheduleRun.id == run_id)
            .values(
                status=status.value,
                target_run_id=target_run_id,
                error_type=error_type,
                error_code=error_code,
                completed_at=datetime.now(timezone.utc),
            )
        )

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

    async def reset_for_retry(
        self, *, schedule_id: UUID, run_id: UUID
    ) -> ScheduleRunEntity | None:
        model = await self.session.scalar(
            select(ScheduleRun)
            .where(ScheduleRun.id == run_id, ScheduleRun.schedule_id == schedule_id)
            .with_for_update()
        )
        if model is None or model.status not in {
            ScheduleRunStatus.FAILED.value,
            ScheduleRunStatus.DEAD_LETTERED.value,
        }:
            return None
        model.status = ScheduleRunStatus.RECEIVED.value
        model.attempts = 0
        model.completed_at = None
        model.error_type = None
        model.error_code = None
        await self.session.flush()
        return model.to_entity()
