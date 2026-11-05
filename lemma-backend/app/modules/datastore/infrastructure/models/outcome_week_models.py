"""Where a pod stood against what it owns, one row per week.

The measure of what a pod owns is a query that answers one number now, and
nothing about any week before this one. So the number is read on a schedule
and kept here: each reading replaces the week's row, and the week's last
reading is what it closed on.

``measure_digest`` says which measure answered. A history is the weeks read by
the measure the pod has now, so changing the measure starts a new one instead
of drawing a different count's weeks behind it -- and putting the old measure
back brings its weeks back.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class PodOutcomeWeekModel(Base):
    __tablename__ = "pod_outcome_weeks"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), primary_key=True
    )
    #: The Monday the week starts on, in UTC.
    starts_on: Mapped[date] = mapped_column(Date, primary_key=True)
    #: SHA-256 of the measure that answered, as hex.
    measure_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: What it answered; null when it answered null, or could not be read.
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: The target when it was read, so a target moved later does not redraw
    #: the weeks before.
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Why it could not be read, in words for the people in the pod.
    problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
