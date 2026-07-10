from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
    AuthConfigStatus,
)
from app.modules.connectors.domain.connector import AuthProvider

if TYPE_CHECKING:
    from app.modules.connectors.infrastructure.models.connector import Connector


class AuthConfig(UUIDAuditBase):
    """Organization-scoped selected provider config for a connector app."""

    __tablename__ = "auth_configs"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), default=AuthProvider.LEMMA.value)
    config_source: Mapped[str] = mapped_column(
        String(50), default=AuthConfigSource.SYSTEM_DEFAULT.value
    )
    status: Mapped[str] = mapped_column(
        String(50), default=AuthConfigStatus.ACTIVE.value
    )
    provider_config: Mapped[dict | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, default=None, nullable=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped[Any] = relationship("Organization")
    connector: Mapped["Connector"] = relationship("Connector")
    created_by_user: Mapped[Any] = relationship("User", foreign_keys=[created_by_user_id])
    updated_by_user: Mapped[Any] = relationship("User", foreign_keys=[updated_by_user_id])

    __table_args__ = (
        Index(
            "ix_auth_configs_unique_active_org_app",
            "organization_id",
            "connector_id",
            unique=True,
            postgresql_where=(status == AuthConfigStatus.ACTIVE.value),
        ),
        Index(
            "ix_auth_configs_unique_active_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=(status == AuthConfigStatus.ACTIVE.value),
        ),
        Index("ix_auth_configs_org_status", "organization_id", "status"),
        Index("ix_auth_configs_app_provider_status", "connector_id", "provider", "status"),
    )

    def to_entity(self) -> AuthConfigEntity:
        return AuthConfigEntity(
            id=self.id,
            organization_id=self.organization_id,
            connector_id=self.connector_id,
            name=self.name,
            provider=self.provider,
            config_source=self.config_source,
            status=self.status,
            provider_config=self.provider_config,
            metadata=self.metadata_,
            created_by_user_id=self.created_by_user_id,
            updated_by_user_id=self.updated_by_user_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
