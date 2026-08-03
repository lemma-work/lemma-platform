from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import StringAuditBase
from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity

if TYPE_CHECKING:
    from .connector import Connector


class ConnectorTrigger(StringAuditBase):
    """Available trigger for connector events."""

    __tablename__ = "connector_triggers"

    connector_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(50), default="package", nullable=False)
    # id serves as the name/slug
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    config_schema: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    payload_schema: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    payload_example: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True
    )

    # Relationships
    connector: Mapped["Connector"] = relationship("Connector")

    __table_args__ = (
        Index(
            "ix_connector_triggers_app_kind_event",
            "connector_id",
            "kind",
            "event_type",
            unique=True,
        ),
    )

    def to_entity(self) -> ConnectorTriggerEntity:
        return ConnectorTriggerEntity.model_validate(self)

    def __repr__(self) -> str:
        return f"<ConnectorTrigger(id={self.id})>"
