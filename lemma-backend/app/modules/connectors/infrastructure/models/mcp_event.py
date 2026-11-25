"""What a connected MCP server offers to tell us, and what we asked it to."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.vault.contracts import vault_owned


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
    and kept in the vault.
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
    #: The signing secret, as a vault secret (purpose
    #: `connectors.event_subscription.secret`, scoped to the org and user).
    signing_secret_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault_secrets.id"), unique=True, nullable=True
    )
    #: The previous release's Fernet column. Read only while a row written by
    #: an old process mid-rollout has no `signing_secret_id`; never written.
    #: Dropped by the next release's contract migration.
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The server's id for it, once it has answered; `X-MCP-Subscription-Id`.
    remote_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    granted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the refresher next renews it: halfway through the grant, or, after
    #: a failed renewal, a retry that waits longer each time.
    renew_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Renewals in a row that failed; a grant resets it.
    renew_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_connector_event_subscriptions_renew", "renew_after"),
        Index("ix_connector_event_subscriptions_account", "account_id"),
    )


vault_owned(ConnectorEventSubscription.__table__, "signing_secret_id")
