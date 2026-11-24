"""The reports table.

It cascades from the pod and from the person who reported: a report is about
what was said in a pod, and holds a copy of it, so it goes when the pod does,
as everything else said there does. The message, conversation, channel and
run it names are not foreign keys: a report outlives the message being
removed, which is often what the team does about it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class ContentReportModel(UUIDAuditBase):
    __tablename__ = "content_reports"
    __table_args__ = (
        # One report per person per message: reporting it again replaces it.
        UniqueConstraint(
            "reporter_user_id", "message_id", name="uq_content_reports_reporter_message"
        ),
        # What the team reads first: what is still open, oldest first.
        Index("ix_content_reports_status_created", "status", "created_at"),
    )

    organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reporter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    place: Mapped[str] = mapped_column(String(16), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    channel_id: Mapped[UUID | None] = mapped_column(nullable=True)
    message_id: Mapped[UUID] = mapped_column(nullable=False)
    place_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    author_id: Mapped[UUID | None] = mapped_column(nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The copy: ``ExcerptLine``s, oldest first, the reported one marked.
    excerpt: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OPEN", server_default="OPEN"
    )
    #: Written by the team when they close it: when, and what they did.
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
