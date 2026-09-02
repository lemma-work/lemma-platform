from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import StringAuditBase
from app.modules.connectors.domain.connector import ConnectorEntity


class Connector(StringAuditBase):
    """Connector model for third-party connectors."""

    __tablename__ = "connectors"

    title: Mapped[str] = mapped_column(String(255), nullable=True, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    icon: Mapped[str | None] = mapped_column(
        String(500), default=None, nullable=True
    )  # File path
    # The ways this connector can be installed (one KindSpec per element). An
    # install picks exactly one; several entries is normal -- gmail, slack, jira
    # and google_drive all ship as both a vendored package and a Composio toolkit.
    kinds: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    agent_instruction: Mapped[str | None] = mapped_column(
        Text, default=None, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def to_entity(self) -> ConnectorEntity:
        return ConnectorEntity.model_validate(self)

    def __str__(self) -> str:
        return self.title or str(self.id)

    def __repr__(self) -> str:
        return f"<Connector(id={self.id})>"
