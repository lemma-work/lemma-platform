"""Web widgets: a pod's chat bubbles and forms on other people's web pages.

A widget answers as one of the pod's agents and is reached by its public key,
which anybody can read off the page that embeds it. The key names the widget
and nothing else; who a visitor is comes from a token the customer's server
signs with the widget's secret, or from a one-time code they entered.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class WebWidgetModel(UUIDAuditBase):
    __tablename__ = "agent_surface_web_widgets"
    __table_args__ = (
        Index("uq_web_widget_pod_name", "pod_id", "name", unique=True),
        Index("uq_web_widget_public_key", "public_key", unique=True),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Encrypted at rest; shown once when minted or rotated.
    signing_secret: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_origins: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    answer: Mapped[str] = mapped_column(String(20), nullable=False)
    looked_after_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    form_function: Mapped[str | None] = mapped_column(String(255), nullable=True)
    form_requires_code: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )


class WebSessionModel(UUIDAuditBase):
    """One visitor's chat with one widget, found by a token only they hold.

    The conversation is the session's, and goes with it: deleting a
    conversation (forgetting a contact) ends the session that led to it.
    """

    __tablename__ = "agent_surface_web_sessions"
    __table_args__ = (
        Index("uq_web_session_token", "token_hash", unique=True),
        Index("ix_web_session_conversation", "conversation_id"),
    )

    widget_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surface_web_widgets.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    contact_strength: Mapped[str | None] = mapped_column(String(20), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WebCodeModel(UUIDAuditBase):
    """A one-time code sent to an email address a visitor typed."""

    __tablename__ = "agent_surface_web_codes"
    __table_args__ = (Index("ix_web_code_session", "session_id", "email"),)

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surface_web_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
