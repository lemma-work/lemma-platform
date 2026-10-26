"""What this module keeps about each conversation it answers on a platform.

Keys of the agent conversation's metadata JSON until migration 0065, which
backfilled this table from them (``domain/conversation_surface``). ``models.py``
re-exports it, so importing that file registers the table for
``migrations/env.py``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base

_NOW = text("now()")


class SurfaceConversationStateModel(Base):
    """The place a conversation is answered at, and two records kept against it.

    ``held_replies`` is what waits for a member to reopen a closed reply window
    (``services/held_replies``); ``free_text_answer_for`` the paused call
    somebody chose to answer by typing (``services/free_text_answer``).
    """

    __tablename__ = "surface_conversation_state"

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), primary_key=True
    )
    surface_id: Mapped[UUID | None] = mapped_column(nullable=True)
    surface_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    conversation_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    external_channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    route_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    routed_agent_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    surface_event_metadata: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    web_widget_id: Mapped[UUID | None] = mapped_column(nullable=True)
    web_session_id: Mapped[UUID | None] = mapped_column(nullable=True)
    held_replies: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    free_text_answer_for: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


__all__ = ["SurfaceConversationStateModel"]
