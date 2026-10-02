"""The events a schedule's triage let act straight away, kept to bound the rate."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase


class ScheduleActAdmission(UUIDCreatedBase):
    """One event admitted to act under `act_per_hour`, at `created_at`.

    Written before the event's fire is published, under a lock on the
    schedule, so the count a second event reads already includes the first.
    The run ledger cannot serve: an act's run is written downstream of the
    fire, after concurrent events have all read the same count.
    """

    __tablename__ = "schedule_act_admissions"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "source_event_id", name="uq_schedule_act_admissions_event"
        ),
        Index("ix_schedule_act_admissions_window", "schedule_id", "created_at"),
    )

    schedule_id: Mapped[UUID] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
