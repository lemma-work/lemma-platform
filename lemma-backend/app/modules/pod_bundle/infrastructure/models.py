"""PostgreSQL-authoritative pod-bundle job and checkpoint models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class PodBundleJob(UUIDAuditBase):
    __tablename__ = "pod_bundle_jobs"

    # Override the shared base's legacy PK index; a primary-key btree already
    # supports every lookup by id.
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, index=False)
    job_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    committed_steps: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_pod_bundle_jobs_active_recovery",
            "job_kind",
            "status",
            "heartbeat_at",
            postgresql_where=text(
                "status IN ('QUEUED', 'FETCHING', 'PLANNING', 'APPLYING', "
                "'CANCELLING', 'EXPORTING', 'PUBLISHING')"
            ),
        ),
        Index(
            "ix_pod_bundle_jobs_pod_history",
            "pod_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_pod_bundle_jobs_completed_retention",
            "completed_at",
            postgresql_where=text("completed_at IS NOT NULL"),
        ),
    )


class PodBundleJobStep(UUIDAuditBase):
    __tablename__ = "pod_bundle_job_steps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, index=False)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("pod_bundle_jobs.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("uq_pod_bundle_job_step", "job_id", "step_index", unique=True),
    )
