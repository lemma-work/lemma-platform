"""Tables for deciders, their versions, the decisions they make and their examples."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
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
from pydantic import JsonValue

from app.core.infrastructure.db.base import UUIDAuditBase, UUIDCreatedBase


class DeciderModel(UUIDAuditBase):
    """A pod decider. `definition` is its current version, copied for reads."""

    __tablename__ = "deciders"
    __table_args__ = (UniqueConstraint("pod_id", "name", name="uq_deciders_pod_name"),)

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'POD'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)


class DeciderVersionModel(UUIDCreatedBase):
    """Every saved definition, never edited: a decision names the one it used."""

    __tablename__ = "decider_versions"
    __table_args__ = (
        UniqueConstraint("decider_id", "version", name="uq_decider_versions_version"),
    )

    decider_id: Mapped[UUID] = mapped_column(
        ForeignKey("deciders.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class DecisionModel(UUIDAuditBase):
    """One decision. Unique per (pod, decider, subject, owner): asked once per namespace."""

    __tablename__ = "decisions"
    __table_args__ = (
        Index(
            "uq_decisions_subject",
            "pod_id",
            "decider_key",
            "subject_key",
            "subject_owner_id",
            unique=True,
            postgresql_where=text("subject_key IS NOT NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_decisions_pod_created", "pod_id", "created_at"),
        Index("ix_decisions_evidence_expiry", "evidence_expires_at"),
    )

    pod_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=True
    )
    organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    decider_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    decider_key: Mapped[str] = mapped_column(String(128), nullable=False)
    decider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decider_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: The asker of a PERSONAL decision, null for a POD one. No foreign key: a
    #: deleted user's records must not fall into the pod's shared namespace.
    subject_owner_id: Mapped[UUID | None] = mapped_column(nullable=True)
    shape: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    answers: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
    open: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    trace: Mapped[list[JsonValue]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    answered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DecisionExampleModel(UUIDCreatedBase):
    """What a person said an answer was, kept for the decider that asked."""

    __tablename__ = "decision_examples"
    __table_args__ = (
        Index(
            "ix_decision_examples_lookup",
            "pod_id",
            "decider_key",
            "question_key",
            "created_at",
        ),
    )

    pod_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=True
    )
    decider_key: Mapped[str] = mapped_column(String(128), nullable=False)
    question_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The corrected decision's: a PERSONAL example is shown only to `user_id`.
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True
    )
