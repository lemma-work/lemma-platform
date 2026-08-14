"""Durable schedule-run dispatch ledger."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.schedule.domain.schedule import (
    ScheduleRunEntity,
    ScheduleRunStatus,
)


class ScheduleRun(UUIDAuditBase):
    __tablename__ = "schedule_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, index=False)

    schedule_id: Mapped[UUID] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScheduleRunStatus.RECEIVED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    redrive_of_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("schedule_runs.id", ondelete="SET NULL"), nullable=True
    )
    redriven_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fire_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    llm_output: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "source_event_id", name="uq_schedule_run_source_event"
        ),
        Index(
            "ix_schedule_runs_schedule_created",
            "schedule_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "uq_schedule_runs_target",
            "target_kind",
            "target_run_id",
            unique=True,
            postgresql_where=text("target_run_id IS NOT NULL"),
        ),
        Index(
            "uq_schedule_runs_redrive",
            "redrive_of_run_id",
            unique=True,
            postgresql_where=text("redrive_of_run_id IS NOT NULL"),
        ),
        # Serves the five-minute recovery sweep. There was already an index for
        # that sweep -- ix_schedule_runs_retryable_recovery, created in 0003 --
        # but its predicate covered RECEIVED/PROCESSING/FAILED while the query
        # asks about PROCESSING/FAILED/DISPATCHED. Postgres cannot use a partial
        # index it cannot prove covers the query, so it never did: zero scans in
        # production while the sweep sequentially scanned the table.
        #
        # It was also declared only in the migration and not here, so databases
        # built by create_all -- every test database -- never had it at all.
        #
        # The status set is what keeps this small: terminal runs are almost the
        # whole table and none of them can match. Leading with (updated_at, id)
        # answers the query's ORDER BY from the index instead of sorting.
        Index(
            "ix_schedule_runs_recoverable",
            "updated_at",
            "id",
            postgresql_where=text(
                "target_outcome IS NULL "
                "AND status IN ('PROCESSING', 'FAILED', 'DISPATCHED')"
            ),
        ),
    )

    def to_entity(self) -> ScheduleRunEntity:
        return ScheduleRunEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            schedule_id=self.schedule_id,
            user_id=self.user_id,
            source_event_id=self.source_event_id,
            status=ScheduleRunStatus(self.target_outcome or self.status),
            attempts=self.attempts,
            target_kind=self.target_kind,
            target_run_id=self.target_run_id,
            redrive_of_run_id=self.redrive_of_run_id,
            redriven_by_user_id=self.redriven_by_user_id,
            payload=self.payload or {},
            metadata=self.fire_metadata or {},
            llm_output=self.llm_output or {},
            error_type=self.error_type,
            error_code=self.error_code,
            source_occurred_at=self.source_occurred_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
