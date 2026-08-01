"""Persistence operations for durable schedule-run dispatch."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

from sqlalchemy import select
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
            .on_conflict_do_nothing(constraint="uq_schedule_run_source_event")
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
        if model.user_id is None:
            model.user_id = user_id
        if model.target_run_id is None:
            model.target_run_id = str(uuid7())
        await self.session.flush()
        if model.target_outcome is not None:
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

    async def mark_dispatched(self, run_id: UUID) -> None:
        """Mark launch complete unless a synchronous target outcome won the race.

        The ``PROCESSING`` predicate is the whole mechanism: a target that
        already finished has moved the row to a terminal state, and dispatch
        must not drag it back. Callers get no result because there is nothing
        to decide — the ledger row is the authority either way.
        """
        model = await self.session.get(ScheduleRun, run_id, with_for_update=True)
        if model is None or model.status != ScheduleRunStatus.PROCESSING.value:
            return
        model.status = ScheduleRunStatus.DISPATCHED.value
        if model.target_outcome is None:
            model.error_type = None
            model.error_code = None
            model.completed_at = None
        await self.session.flush()

    async def mark_failed(self, run_id: UUID, exc: Exception) -> ScheduleRunStatus:
        model = await self.session.get(ScheduleRun, run_id, with_for_update=True)
        if model is None:
            raise LookupError(f"Schedule run {run_id} no longer exists")
        if model.target_outcome is not None:
            return ScheduleRunStatus(model.target_outcome)
        if model.status in {
            ScheduleRunStatus.COMPLETED.value,
            ScheduleRunStatus.TARGET_FAILED.value,
            ScheduleRunStatus.CANCELLED.value,
            ScheduleRunStatus.DEAD_LETTERED.value,
        }:
            return ScheduleRunStatus(model.status)
        if model.status != ScheduleRunStatus.PROCESSING.value:
            raise RuntimeError(f"Cannot fail schedule run {run_id} from {model.status}")
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
        if model is None or model.target_outcome is not None:
            return None

        if model.status in {
            ScheduleRunStatus.COMPLETED.value,
            ScheduleRunStatus.TARGET_FAILED.value,
            ScheduleRunStatus.CANCELLED.value,
        }:
            model.target_outcome = model.status
            await self.session.flush()
            return model.to_entity()
        if model.status not in {
            ScheduleRunStatus.PROCESSING.value,
            ScheduleRunStatus.DISPATCHED.value,
        }:
            return None

        model.target_outcome = status.value
        model.error_type = error_type
        model.error_code = None
        model.completed_at = completed_at or datetime.now(timezone.utc)
        await self.session.flush()
        return model.to_entity()

    async def consecutive_terminal_failures(self, schedule_id: UUID) -> int:
        rows = (
            await self.session.execute(
                select(ScheduleRun.status, ScheduleRun.target_outcome)
                .where(
                    ScheduleRun.schedule_id == schedule_id,
                    ScheduleRun.completed_at.is_not(None),
                )
                .order_by(ScheduleRun.completed_at.desc(), ScheduleRun.id.desc())
            )
        ).all()
        failures = 0
        for dispatch_status, target_outcome in rows:
            effective_status = target_outcome or dispatch_status
            if effective_status in {
                ScheduleRunStatus.COMPLETED.value,
                ScheduleRunStatus.CANCELLED.value,
            }:
                break
            if effective_status in {
                ScheduleRunStatus.TARGET_FAILED.value,
                ScheduleRunStatus.DEAD_LETTERED.value,
            }:
                failures += 1
        return failures

    async def create_redrive(
        self,
        *,
        schedule_id: UUID,
        run_id: UUID,
        redriven_by_user_id: UUID,
        fallback_user_id: UUID,
        required_user_id: UUID | None = None,
    ) -> tuple[ScheduleRunEntity, bool] | None:
        source_query = select(ScheduleRun).where(
            ScheduleRun.id == run_id,
            ScheduleRun.schedule_id == schedule_id,
        )
        if required_user_id is not None:
            source_query = source_query.where(ScheduleRun.user_id == required_user_id)
        source = await self.session.scalar(source_query.with_for_update())
        if source is None:
            return None

        existing = await self.session.scalar(
            select(ScheduleRun).where(ScheduleRun.redrive_of_run_id == source.id)
        )
        if existing is not None:
            return existing.to_entity(), False

        effective_status = source.target_outcome or source.status
        if effective_status not in {
            ScheduleRunStatus.FAILED.value,
            ScheduleRunStatus.DEAD_LETTERED.value,
            ScheduleRunStatus.TARGET_FAILED.value,
        }:
            return None

        redrive = ScheduleRun(
            schedule_id=schedule_id,
            user_id=source.user_id or fallback_user_id,
            source_event_id=f"redrive:{source.id}",
            status=ScheduleRunStatus.RECEIVED.value,
            attempts=0,
            target_kind=source.target_kind,
            target_run_id=str(uuid7()),
            payload=source.payload or {},
            fire_metadata=source.fire_metadata or {},
            llm_output=source.llm_output or {},
            source_occurred_at=source.source_occurred_at,
            redrive_of_run_id=source.id,
            redriven_by_user_id=redriven_by_user_id,
        )
        self.session.add(redrive)
        await self.session.flush()
        return redrive.to_entity(), True

    async def list_for_schedule(
        self,
        schedule_id: UUID,
        *,
        limit: int = 100,
        user_id: UUID | None = None,
    ) -> list[ScheduleRunEntity]:
        stmt = select(ScheduleRun).where(ScheduleRun.schedule_id == schedule_id)
        if user_id is not None:
            stmt = stmt.where(ScheduleRun.user_id == user_id)
        rows = await self.session.scalars(
            stmt.order_by(ScheduleRun.created_at.desc(), ScheduleRun.id.desc()).limit(
                limit
            )
        )
        return [row.to_entity() for row in rows.all()]
