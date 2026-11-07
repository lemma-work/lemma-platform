"""Persistence for what machines add to sandboxes: previews, app consents,
metered sessions and login backups."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class MachinePreviewModel(UUIDAuditBase):
    """A port on a machine, reachable at ``p-<label>.<MACHINE_PREVIEW_DOMAIN>``.

    A row rather than a signed token so a preview can be revoked before it
    expires: the proxy checks the row (through a short Redis cache) on every
    request, and a revoked or expired row answers 404.
    """

    __tablename__ = "machine_previews"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'link')",
            name="ck_machine_previews_visibility",
        ),
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_machine_previews_port"),
        UniqueConstraint("label", name="uq_machine_previews_label"),
        Index("ix_machine_previews_sandbox", "sandbox_id"),
    )

    sandbox_id: Mapped[UUID] = mapped_column(
        ForeignKey("sandboxes.id", ondelete="CASCADE"), nullable=False
    )
    # The subdomain. Random, because a `link` preview's URL is its credential.
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[UUID] = mapped_column(nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AppMachineConsentModel(UUIDAuditBase):
    """A person's yes to one app running code on their default machine."""

    __tablename__ = "app_machine_consents"
    __table_args__ = (
        Index(
            "uq_app_machine_consents_live",
            "user_id",
            "app_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    app_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MachineSessionModel(UUIDAuditBase):
    """One stretch of a machine running, metered in slices.

    Opened when a machine is provisioned or resumed, closed on release or
    destroy. ``metered_until`` is how far its compute has been written to the
    usage ledger; a slice is keyed ``(session, slice start)`` so the sweeper
    and a release racing each other write it once.
    """

    __tablename__ = "machine_sessions"
    __table_args__ = (
        CheckConstraint(
            "billing_scope IN ('user', 'org')",
            name="ck_machine_sessions_billing_scope",
        ),
        Index(
            "ix_machine_sessions_open",
            "sandbox_id",
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    sandbox_id: Mapped[UUID] = mapped_column(
        ForeignKey("sandboxes.id", ondelete="CASCADE"), nullable=False
    )
    size: Mapped[str] = mapped_column(String(16), nullable=False)
    compute_units_per_hour: Mapped[float] = mapped_column(Float, nullable=False)
    billing_scope: Mapped[str] = mapped_column(String(8), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pod_id: Mapped[UUID | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    metered_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MachineLoginBackupModel(UUIDAuditBase):
    """An encrypted copy of the login-bearing parts of a browser profile.

    The object holds AES-GCM ciphertext; ``wrapped_key`` is its data key under
    the deployment's secret cipher, so a leaked bucket alone opens nothing.
    """

    __tablename__ = "machine_login_backups"
    __table_args__ = (
        Index("ix_machine_login_backups_sandbox", "sandbox_id", "created_at"),
    )

    sandbox_id: Mapped[UUID] = mapped_column(
        ForeignKey("sandboxes.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    wrapped_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Why it was taken: a clean close, the daily copy, or before a reset.
    reason: Mapped[str] = mapped_column(String(32), nullable=False)


class MachineConnectionModel(UUIDAuditBase):
    """A person's own computer, joined to Lemma with the CLI.

    The table arrives with machines so every machine table is in one
    migration; the link that writes it, pairing and revocation, comes with
    connected machines. A machine row (`sandboxes`) names its connection by
    `connection_id`, deliberately without a foreign key: the machine row
    outlives a revoked connection, as every machine row outlives its compute.
    """

    __tablename__ = "machine_connections"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('host', 'image')", name="ck_machine_connections_mode"
        ),
        UniqueConstraint("secret_hash", name="uq_machine_connections_secret_hash"),
        Index(
            "uq_machine_connections_live_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    #: The pod it is shared into, when its owner shared it.
    pod_id: Mapped[UUID | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    home: Mapped[str] = mapped_column(Text, nullable=False)
    runtime_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    link_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
