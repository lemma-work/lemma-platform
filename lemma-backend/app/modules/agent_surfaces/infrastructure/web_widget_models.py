"""Web widgets: a pod's chat on other people's web pages.

A widget answers as one of the pod's agents and is reached by its public key,
which anybody can read off the page that embeds it. The key names the widget
and nothing else; who a visitor is comes from a token the customer's server
signs with the widget's secret, or from a one-time code they entered -- kept
on their session, which is contacts' (``contacts/infrastructure/visitor_sessions``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.vault.contracts import vault_owned


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
    #: Shown once when minted or rotated; kept as a vault secret (purpose
    #: ``agent_surfaces.web_widget.signing_secret``, scoped to the pod).
    signing_secret_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("vault_secrets.id"), unique=True, nullable=True
    )
    #: The previous release's Fernet column. Read only while a row written by
    #: an old process mid-rollout has no ``signing_secret_id``; written only to
    #: NULL. Dropped by the next release's contract migration.
    signing_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_origins: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    answer: Mapped[str] = mapped_column(String(20), nullable=False)
    looked_after_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: The product whose users this widget's host tokens name: a host contact
    #: is ``host:{host_issuer}:{sub}``. Widgets of one product share it.
    host_issuer: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'default'")
    )


vault_owned(WebWidgetModel.__table__, "signing_secret_id")
