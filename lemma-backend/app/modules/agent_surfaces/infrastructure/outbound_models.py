"""What the bot sent, by the id the platform gave it.

Mirrors ``migrations/versions/2026-10-04_surface_outbound_messages_0044.py``
column for column and index name for index name, for the reason
``whatsapp_pool_models`` gives. Its own module because ``models.py`` is at the
size ceiling.

Two readers. A delivery-status webhook names a message only by its platform id,
so a failure reported minutes later can be traced back to the conversation and
the notification it belonged to. And a person quoting one of the bot's messages
names it the same way, so the run can be shown what they are pointing at.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase

#: The two kinds of send that are traced back: an answer in a conversation, and
#: a notification delivered into one.
OUTBOUND_KIND_REPLY = "REPLY"
OUTBOUND_KIND_NOTIFICATION = "NOTIFICATION"
#: Appended to the kind of every message after the first of one delivery.
OUTBOUND_PART_SUFFIX = "_PART"

OUTBOUND_STATUS_SENT = "SENT"
OUTBOUND_STATUS_FAILED = "FAILED"


class AgentSurfaceOutboundMessageModel(UUIDAuditBase):
    __tablename__ = "agent_surface_outbound_messages"
    __table_args__ = (
        # Every read starts from the id the platform reported, and a send is
        # recorded once however often its status arrives.
        Index(
            "ix_agent_surface_outbound_platform_message",
            "platform",
            "external_message_id",
            unique=True,
        ),
        # The retention sweep's predicate.
        Index("ix_agent_surface_outbound_created", "created_at"),
    )

    surface_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    notification_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    external_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "OUTBOUND_KIND_NOTIFICATION",
    "OUTBOUND_PART_SUFFIX",
    "OUTBOUND_KIND_REPLY",
    "OUTBOUND_STATUS_FAILED",
    "OUTBOUND_STATUS_SENT",
    "AgentSurfaceOutboundMessageModel",
]
