"""Persistence models for Agent Host identity, discovery, and dispatch."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import Base, UUIDAuditBase, UUIDCreatedBase


class AgentHostPairingModel(UUIDCreatedBase):
    """Single-use user-authorized Agent Host pairing code.

    Consuming a code deletes the row, so presence is the whole validity
    check: there is no consumed flag to interpret, and a replayed code is
    indistinguishable from one that never existed.
    """

    __tablename__ = "agent_host_pairings"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_agent_host_pairing_code_hash"),
        # Covers user_id-only lookups as well, so user_id carries no separate
        # single-column index.
        Index("ix_agent_host_pairing_user_expires", "user_id", "expires_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AgentHostModel(UUIDAuditBase):
    """One authenticated Agent Host installation paired to this target."""

    __tablename__ = "agent_hosts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "organization_id",
            "installation_id",
            name="uq_agent_host_user_org_installation",
            # Requires PostgreSQL 15+; without it a personal host
            # (organization_id IS NULL) would not collide with itself.
            postgresql_nulls_not_distinct=True,
        ),
        UniqueConstraint(
            "host_secret_hash",
            name="uq_agent_host_secret_hash",
        ),
        # Covers user_id-only lookups as well, so user_id carries no separate
        # single-column index.
        Index("ix_agent_host_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    installation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    host_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Indexed on its own for the cross-user offline sweep, which has no
    # user_id predicate to lead with.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="OFFLINE", index=True
    )
    protocol_version: Mapped[int | None] = mapped_column(nullable=True)
    host_release: Mapped[str] = mapped_column(String(128), nullable=False)
    capacity: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[Any] = relationship("User", foreign_keys=[user_id])
    organization: Mapped[Any] = relationship(
        "Organization", foreign_keys=[organization_id]
    )


class AgentHostHarnessModel(UUIDAuditBase):
    """Revisioned capability/configuration snapshot for one local harness."""

    __tablename__ = "agent_host_harnesses"
    __table_args__ = (
        UniqueConstraint(
            "host_id",
            "harness_key",
            name="uq_agent_host_harness_key",
        ),
    )

    host_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_hosts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    harness_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(128), nullable=False)
    upstream_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    health: Mapped[str] = mapped_column(String(64), nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    config_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    config_options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    stale_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    stale_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    host: Mapped[AgentHostModel] = relationship(
        "AgentHostModel", foreign_keys=[host_id]
    )


class AgentHostCommandModel(UUIDCreatedBase):
    """Durable at-least-once command for an Agent Host."""

    __tablename__ = "agent_host_commands"
    __table_args__ = (
        # Drives the competitive FOR UPDATE SKIP LOCKED handout.
        Index("ix_agent_host_command_poll", "host_id", "state", "created_at"),
        Index("ix_agent_host_command_run", "run_id", "lease_epoch"),
    )

    host_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_hosts.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AgentHostRunLeaseModel(Base):
    """The single dispatch fence for one agent run.

    ``run_id`` is the primary key, which is what makes double-dispatch
    structurally impossible rather than merely guarded in application code.
    There is no ack-watermark column: run events travel a per-run Redis Stream
    whose last entry is the watermark.
    """

    __tablename__ = "agent_host_run_leases"
    __table_args__ = (
        # Also serves host_id-only lookups.
        Index("ix_agent_host_run_lease_host_state", "host_id", "state"),
        Index(
            "ix_agent_host_run_lease_expiry",
            "lease_expires_at",
            postgresql_where=text(
                "state NOT IN ('WAITING_INPUT','SUCCEEDED','FAILED',"
                "'CANCELLED','DISPATCH_UNKNOWN')"
            ),
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    host_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_hosts.id", ondelete="CASCADE"),
        nullable=False,
    )
    harness_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_host_harnesses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    runtime_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runtime_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
