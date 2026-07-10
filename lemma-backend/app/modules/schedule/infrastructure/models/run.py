"""Durable schedule-run dispatch ledger."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
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
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ScheduleRunStatus.RECEIVED.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fire_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    llm_output: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
            "ix_schedule_runs_retryable_recovery",
            "status",
            "updated_at",
            "schedule_id",
            postgresql_where=text("status IN ('RECEIVED', 'PROCESSING', 'FAILED')"),
        ),
    )

    def to_entity(self) -> ScheduleRunEntity:
        return ScheduleRunEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            schedule_id=self.schedule_id,
            source_event_id=self.source_event_id,
            status=ScheduleRunStatus(self.status),
            attempts=self.attempts,
            target_kind=self.target_kind,
            target_run_id=self.target_run_id,
            payload=self.payload or {},
            metadata=self.fire_metadata or {},
            llm_output=self.llm_output or {},
            error_type=self.error_type,
            error_code=self.error_code,
            source_occurred_at=self.source_occurred_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )
