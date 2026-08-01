from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import StringAuditBase
from app.modules.connectors.domain.connector_operation import (
    ConnectorOperationEntity,
)

if TYPE_CHECKING:
    from .connector import Connector


class ConnectorOperation(StringAuditBase):
    """Global catalog operation, seeded from the release by the import script.

    This table holds *only* connector-wide operations -- Composio toolkit tools,
    vendored package operations, and specs bundled at import time. Operations
    discovered per install (MCP tools, OpenAPI-URL endpoints) live in
    ``auth_config_operations`` instead, so a catalog query can never return one
    tenant's data to another. That separation is structural, not a predicate
    somebody has to remember to write.
    """

    __tablename__ = "connector_operations"

    connector_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("connectors.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(50), default="package", nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_operation_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict | None] = mapped_column(
        JSONB,
        default=None,
        nullable=True,
    )
    output_schema: Mapped[dict | None] = mapped_column(
        JSONB,
        default=None,
        nullable=True,
    )
    # Polymorphic execution descriptor consumed by the kind's executor. NULL for
    # `package`, whose operations are described by the vendored client itself.
    execution: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True)

    connector: Mapped["Connector"] = relationship("Connector")

    __table_args__ = (
        Index(
            "uq_connector_operations_name",
            "connector_id",
            "kind",
            "name",
            unique=True,
        ),
        Index(
            "ix_connector_operations_app_kind_operation",
            "connector_id",
            "kind",
            "provider_operation_name",
        ),
    )

    def to_entity(self) -> ConnectorOperationEntity:
        return ConnectorOperationEntity.model_validate(self)

    def __repr__(self) -> str:
        return (
            f"<ConnectorOperation(connector_id={self.connector_id}, name={self.name})>"
        )
