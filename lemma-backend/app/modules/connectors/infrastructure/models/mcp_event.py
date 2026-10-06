"""What a connected MCP server offers to tell us, and what we asked it to."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class AuthConfigEvent(UUIDAuditBase):
    """One event an install's server lists, kept like its discovered tools:
    tenant data, per install, never in the global trigger catalog."""

    __tablename__ = "auth_config_events"

    auth_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    payload_schema: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        Index("uq_auth_config_events_name", "auth_config_id", "name", unique=True),
        Index("ix_auth_config_events_org", "organization_id"),
    )


class ConnectorEventSubscription(UUIDAuditBase):
    """One subscription made on a person's account, for one schedule.

    `id` is ours and rides in the callback URL, which is how a delivery -- and
    the challenge that arrives before the server has named its own id -- finds
    the secret to check it with. The secret is ours too, made per subscription
    and stored encrypted.
    """

    __tablename__ = "connector_event_subscriptions"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    auth_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    #: The server's id for it, once it has answered; `X-MCP-Subscription-Id`.
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    granted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_connector_event_subscriptions_refresh", "refresh_before"),
        Index("ix_connector_event_subscriptions_account", "account_id"),
    )
