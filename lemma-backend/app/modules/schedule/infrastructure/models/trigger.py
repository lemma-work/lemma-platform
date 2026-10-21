"""What a trigger skipped, and the batches it planned.

Both are written by the trigger machinery in the transaction that decides, and
both are keyed so a replayed page or batch writes the same row again rather
than a second one.
"""

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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class TriggerSkip(Base):
    """Changes or fires a trigger let pass without firing, aggregated.

    One row per (page, reason), not per change: a bulk write of thousands of
    rows is a handful of rows here, and the trigger's history can still say
    what was skipped and why. ``first``/``last`` are positions in the source
    -- a change's ``xid:seq``, or a fire's source event id.
    """

    __tablename__ = "trigger_skips"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    schedule_id: Mapped[UUID] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    first: Mapped[str] = mapped_column(String(255), nullable=False)
    last: Mapped[str] = mapped_column(String(255), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "schedule_id", "reason", "first", name="uq_trigger_skips_page"
        ),
        Index("ix_trigger_skips_schedule_at", "schedule_id", "at"),
    )


class TriggerBatch(Base):
    """A batch a ``batch``-mode trigger planned before firing it.

    Recorded first, so a replay after a crash finds the window it already cut
    and fires that one -- never a second, differently cut batch.
    """

    __tablename__ = "trigger_batches"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    schedule_id: Mapped[UUID] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    first: Mapped[str] = mapped_column(String(255), nullable=False)
    last: Mapped[str] = mapped_column(String(255), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("schedule_id", "first", name="uq_trigger_batches_first"),
    )
