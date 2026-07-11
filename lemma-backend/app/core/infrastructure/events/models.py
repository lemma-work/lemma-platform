"""Durable transactional event delivery models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase


class DomainEventOutbox(UUIDCreatedBase):
    __tablename__ = "domain_event_outbox"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, index=False)

    stream: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    producer: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    causation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_domain_event_outbox_ready",
            "available_at",
            "occurred_at",
            "id",
            postgresql_where=text("published_at IS NULL AND dead_lettered_at IS NULL"),
        ),
        Index(
            "ix_domain_event_outbox_expired_lease",
            "lease_until",
            "occurred_at",
            "id",
            postgresql_where=text(
                "lease_until IS NOT NULL AND published_at IS NULL "
                "AND dead_lettered_at IS NULL"
            ),
        ),
        Index(
            "ix_domain_event_outbox_published_retention",
            "published_at",
            postgresql_where=text("published_at IS NOT NULL"),
        ),
        Index(
            "ix_domain_event_outbox_dlq_listing",
            text("dead_lettered_at DESC"),
            text("id DESC"),
            postgresql_where=text("dead_lettered_at IS NOT NULL"),
        ),
        Index("ix_domain_event_outbox_occurred", text("occurred_at DESC"), text("id DESC")),
    )


class DomainEventInbox(UUIDCreatedBase):
    __tablename__ = "domain_event_inbox"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7, index=False)

    consumer: Mapped[str] = mapped_column(String(200), nullable=False)
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PROCESSING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("consumer", "event_id", name="uq_domain_event_inbox_consumer_event"),
        Index("ix_domain_event_inbox_status_received", "status", "last_received_at"),
        Index(
            "ix_domain_event_inbox_completed_retention",
            "completed_at",
            postgresql_where=text("completed_at IS NOT NULL"),
        ),
        Index(
            "ix_domain_event_inbox_dlq_retention",
            "dead_lettered_at",
            postgresql_where=text("dead_lettered_at IS NOT NULL"),
        ),
    )
