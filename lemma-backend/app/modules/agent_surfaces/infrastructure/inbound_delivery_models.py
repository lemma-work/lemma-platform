"""Tables for inbound messages, outbound deliveries, message claims and receiver cursors.

Their own module because ``models.py`` is at the size ceiling. ``models.py``
re-exports them, so importing that file still registers the tables on
``Base.metadata`` for ``migrations/env.py``. Migration
``0056_agent_surface_bundle_work`` creates them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base

_NOW = text("now()")


class SurfaceInboundMessageModel(Base):
    __tablename__ = "surface_inbound_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RECEIVED', 'INGESTING', 'ROUTED', 'DROPPED', 'FAILED')",
            name="ck_surface_inbound_messages_status",
        ),
        UniqueConstraint(
            "receiver_scope",
            "source_event_id",
            name="uq_surface_inbound_messages_receiver_event",
        ),
        Index("ix_surface_inbound_messages_status_received", "status", "received_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    receiver_scope: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    surface_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pod_id: Mapped[UUID | None] = mapped_column(nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    headers: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    receiver_surface_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    thread_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'RECEIVED'")
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    routed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class SurfaceMessageClaimModel(Base):
    __tablename__ = "surface_message_claims"
    __table_args__ = (Index("ix_surface_message_claims_created_at", "created_at"),)

    claim_key: Mapped[str] = mapped_column(Text, primary_key=True)
    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class SurfaceDeliveryModel(Base):
    __tablename__ = "surface_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'SENT', 'UNKNOWN', 'FAILED')",
            name="ck_surface_deliveries_status",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_surface_deliveries_idempotency_key"
        ),
        Index(
            "ix_surface_deliveries_destination_open",
            "destination_key",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'SENDING')"),
        ),
        Index("ix_surface_deliveries_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    pod_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    notification_id: Mapped[UUID | None] = mapped_column(nullable=True)
    inbound_message_id: Mapped[UUID | None] = mapped_column(nullable=True)
    surface_id: Mapped[UUID | None] = mapped_column(nullable=True)
    destination_key: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'PENDING'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    not_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    sending_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class SurfaceReceiverStateModel(Base):
    __tablename__ = "surface_receiver_state"

    receiver_key: Mapped[str] = mapped_column(Text, primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


__all__ = [
    "SurfaceDeliveryModel",
    "SurfaceInboundMessageModel",
    "SurfaceMessageClaimModel",
    "SurfaceReceiverStateModel",
]
