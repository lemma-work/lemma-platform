from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import String, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)

if TYPE_CHECKING:
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
    from .connector import Connector


class ConnectRequest(UUIDAuditBase):
    """Request to connect a user to a connector."""

    __tablename__ = "connect_requests"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    auth_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("connectors.id", ondelete="CASCADE")
    )

    authorization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default=ConnectRequestStatus.PENDING.value
    )
    attributes: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True
    )

    # Relationships
    connector: Mapped["Connector"] = relationship("Connector")
    auth_config: Mapped["AuthConfig"] = relationship("AuthConfig")
    organization: Mapped[Any] = relationship("Organization")
    user: Mapped[Any] = relationship("User")

    def to_entity(self) -> ConnectRequestEntity:
        return ConnectRequestEntity.model_validate(self)

    def __repr__(self) -> str:
        return f"<ConnectRequest(id={self.id}, user_id={self.user_id}, status={self.status})>"
