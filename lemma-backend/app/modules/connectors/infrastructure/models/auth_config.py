from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigSource,
    AuthConfigStatus,
)
from app.modules.connectors.domain.connector import ConnectorKind

if TYPE_CHECKING:
    from app.modules.connectors.infrastructure.models.connector import Connector


class AuthConfig(UUIDAuditBase):
    """One organization's install of a connector.

    ``kind`` is the single runtime discriminator (it replaced ``provider``). An
    org may hold many active installs of the same connector -- two Slack apps,
    several MCP servers -- so there is deliberately no ``(organization_id,
    connector_id)`` uniqueness. Installs are told apart by ``name``; exactly one
    may be ``is_default``, which is what a caller addressing a bare
    ``connector_id`` resolves to.
    """

    __tablename__ = "auth_configs"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    connector_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("connectors.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(50), default=ConnectorKind.PACKAGE.value, nullable=False
    )
    config_source: Mapped[str] = mapped_column(
        String(50), default=AuthConfigSource.SYSTEM_DEFAULT.value
    )
    status: Mapped[str] = mapped_column(
        String(50), default=AuthConfigStatus.ACTIVE.value
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    config: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True)
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
    created_by_user: Mapped[Any] = relationship(
        "User", foreign_keys=[created_by_user_id]
    )
    updated_by_user: Mapped[Any] = relationship(
        "User", foreign_keys=[updated_by_user_id]
    )

    __table_args__ = (
        Index(
            "ix_auth_configs_unique_active_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=(status == AuthConfigStatus.ACTIVE.value),
        ),
        # At most one default install per (org, connector). This replaces the
        # old unique (organization_id, connector_id) index: many installs are
        # now legal, but exactly one answers a bare connector_id lookup.
        Index(
            "uq_auth_configs_default_per_connector",
            "organization_id",
            "connector_id",
            unique=True,
            postgresql_where=(is_default & (status == AuthConfigStatus.ACTIVE.value)),
        ),
        Index("ix_auth_configs_org_status", "organization_id", "status"),
        Index(
            "ix_auth_configs_org_connector_status",
            "organization_id",
            "connector_id",
            "status",
        ),
    )

    def to_entity(self) -> AuthConfigEntity:
        return AuthConfigEntity(
            id=self.id,
            organization_id=self.organization_id,
            connector_id=self.connector_id,
            name=self.name,
            kind=self.kind,
            config_source=self.config_source,
            status=self.status,
            is_default=self.is_default,
            config=self.config,
            metadata=self.metadata_,
            created_by_user_id=self.created_by_user_id,
            updated_by_user_id=self.updated_by_user_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
