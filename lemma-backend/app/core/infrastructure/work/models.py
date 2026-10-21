"""The work table: live work only, plus dead items kept for inspection.

Completed items are deleted in the transaction that completes them, so the
table never holds history and every claim is an index probe over a small set.
Readiness is derived from ``work_items`` at claim time and never stored, which is
what makes every requeue path safe: none of them has a flag to remember.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid7

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    Index,
    Integer,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    ns: Mapped[str] = mapped_column(Text, nullable=False)
    cls: Mapped[str] = mapped_column("class", Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    dedup_key: Mapped[str | None] = mapped_column(Text)
    concurrency_key: Mapped[str | None] = mapped_column(Text)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)
    #: Which per-key rate bucket the item draws from (e.g. a schedule id).
    rate_key: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    lease_losses: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    first_unavailable_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    lease_owner: Mapped[str | None] = mapped_column(Text)
    #: The fencing token: one more on every claim. ``lease_owner`` names a
    #: process, so a process that claims an item again after its own lease
    #: expired would otherwise pass every fence its earlier run checks.
    lease_epoch: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        # Coalesces *queued* duplicates only. A wake that arrives while the
        # previous item runs must make a new item, or it is lost.
        Index(
            "work_items_dedup",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL AND state = 'queued'"),
        ),
        Index(
            "work_items_ready",
            "class",
            "ns",
            text("priority DESC"),
            "run_at",
            postgresql_where=text("state = 'queued'"),
        ),
        Index(
            "work_items_running",
            "class",
            "ns",
            postgresql_where=text("state = 'running'"),
        ),
        Index(
            "work_items_keyed",
            "concurrency_key",
            postgresql_where=text("state = 'running' AND concurrency_key IS NOT NULL"),
        ),
        Index(
            "work_items_leases",
            "lease_until",
            postgresql_where=text("state = 'running'"),
        ),
        # Dead items are listed by an operator and swept by retention; neither
        # should scan live work to find them.
        Index(
            "work_items_dead",
            "run_at",
            postgresql_where=text("state = 'dead'"),
        ),
    )


class WorkQueue(Base):
    """One row per (class, namespace) that has had work. No readiness state."""

    __tablename__ = "work_queues"

    cls: Mapped[str] = mapped_column("class", Text, primary_key=True)
    ns: Mapped[str] = mapped_column(Text, primary_key=True)
    max_running: Mapped[int] = mapped_column(Integer, nullable=False)
    last_served: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("'epoch'::timestamptz"),
    )
    paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    #: The namespace's named rates (``{"name": {"limit", "per_seconds"}}``),
    #: written with ``max_running`` from the limit port so the claim reads them
    #: with the row instead of asking the port per round.
    rates: Mapped[dict[str, JsonValue]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        # The claim walks a class's queues least-recently-served first and
        # stops at its LIMIT; without this it sorts every queue of the class on
        # every round.
        Index("work_queues_served", "class", "last_served"),
    )


class WorkRateBucket(Base):
    """One token bucket: a named rate for a namespace, optionally per key.

    No row means a full bucket, so rows exist only for buckets in use, and
    retention drops a row once ``full_at`` has passed.
    """

    __tablename__ = "work_rate_buckets"

    ns: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    cap: Mapped[float] = mapped_column(Double, nullable=False)
    per_second: Mapped[float] = mapped_column(Double, nullable=False)
    tokens: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    full_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("work_rate_buckets_full", "full_at"),)
