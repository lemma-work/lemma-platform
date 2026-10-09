"""Web widgets: a pod's chat on other people's web pages.

A widget answers as one of the pod's agents and is reached by its public key,
which anybody can read off the page that embeds it. The key names the widget
and nothing else; who a visitor is comes from a token the customer's server
signs with the widget's secret, or from a one-time code they entered -- kept
on their session, which is contacts' (``contacts/infrastructure/visitor_sessions``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class WebWidgetModel(UUIDAuditBase):
    __tablename__ = "agent_surface_web_widgets"
    __table_args__ = (
        Index("uq_web_widget_pod_name", "pod_id", "name", unique=True),
        Index("uq_web_widget_public_key", "public_key", unique=True),
        Index("ix_web_widget_agent", "agent_id"),
        Index("ix_web_widget_looked_after_by", "looked_after_by"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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
