"""Tables for a contact's support cases: one episode of the pod answering them,
and what happened in it.

A contact's channel has one *current* case (``channels.current_case_id``) and
at most one open one, which is always the current one. A line written after
the current case closed opens the next; a member reopening a closed case by
hand keeps it. Every change to a case appends a ``channel_case_events`` row, so
time to first response, reopens and resolution time are read, not guessed.
Only a contact's channel has cases: the foreign key carries the channel's kind.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import Base, UUIDAuditBase

#: Every kind of case event, as ``ck_channel_case_events_kind`` holds them.
CASE_EVENT_KINDS = (
    "opened",
    "assigned",
    "handed_back",
    "attention",
    "acknowledged",
    "first_response",
    "status",
    "closed",
    "reopened",
    "merged",
)


class ChannelCaseModel(UUIDAuditBase):
    """One episode of the pod answering a contact."""

    __tablename__ = "channel_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'waiting_on_contact', 'closed')",
            name="ck_channel_cases_status",
        ),
        CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)",
            name="ck_channel_cases_closed_when_closed",
        ),
        CheckConstraint(
            "channel_kind = 'CONTACT'", name="ck_channel_cases_contact_only"
        ),
        # Only a contact's channel has cases: the key names the channel's kind.
        ForeignKeyConstraint(
            ["channel_id", "pod_id", "channel_kind"],
            ["channels.id", "channels.pod_id", "channels.kind"],
            ondelete="CASCADE",
        ),
        # For the channel's ``current_case_id``: its case is one of its own.
        UniqueConstraint("id", "channel_id", name="uq_channel_cases_id_channel"),
        # One open case per channel.
        Index(
            "uq_channel_cases_open",
            "channel_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
        # A channel's cases, newest first.
        Index("ix_channel_cases_channel", "channel_id", "opened_at"),
        # Contacts waiting on a person who asked for one (``needs_person``).
        Index(
            "ix_channel_cases_needs_person",
            "pod_id",
            postgresql_where=text(
                "status = 'open' AND assignee_user_id IS NULL"
                " AND attention_at IS NOT NULL"
            ),
        ),
        # A member's cases (``assignee``), and the users' cascades.
        Index(
            "ix_channel_cases_assignee",
            "assignee_user_id",
            postgresql_where=text("assignee_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_cases_first_response_by",
            "first_response_by_user_id",
            postgresql_where=text("first_response_by_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_cases_closed_by",
            "closed_by_user_id",
            postgresql_where=text("closed_by_user_id IS NOT NULL"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(nullable=False)
    channel_kind: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="CONTACT",
        server_default=text("'CONTACT'"),
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The channel's newest line when it opened: the case is the lines after.
    opened_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default=text("'open'")
    )
    #: Who answers the contact by hand, since when, and when they last wrote:
    #: the hand-back window runs from the later.
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    assignee_last_line_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Since when it has asked for a person, and why.
    attention_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attention_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: When the contact was told a person will reply, this wait: the one
    #: acknowledgement they get until a member writes to them.
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The first member line to the contact in the case.
    first_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_response_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ChannelCaseEventModel(Base):
    """One change to a case, in the order it happened."""

    __tablename__ = "channel_case_events"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{kind}'" for kind in CASE_EVENT_KINDS) + ")",
            name="ck_channel_case_events_kind",
        ),
        Index("ix_channel_case_events_case", "case_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_cases.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The member who made it, when one did; no key, so it outlives them.
    actor_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    #: What it changed to: ``{"status": ...}``, ``{"user_id": ...}``, ...
    value: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    #: Written with its case: the case's row goes in first.
    case: Mapped[ChannelCaseModel] = relationship(lazy="raise")


__all__ = ["CASE_EVENT_KINDS", "ChannelCaseEventModel", "ChannelCaseModel"]
