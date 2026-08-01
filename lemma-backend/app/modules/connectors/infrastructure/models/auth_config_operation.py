from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.connectors.domain.connector_operation import (
    InstallOperationEntity,
)

if TYPE_CHECKING:
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig


class AuthConfigOperation(UUIDAuditBase):
    """An operation discovered for one install, owned by one organization.

    MCP tools and OpenAPI-URL endpoints are tenant data: their names, docstrings
    and input schemas describe a customer's own systems. Keeping them in their
    own table -- rather than as nullable-FK rows alongside the global catalog --
    is what makes it impossible for a catalog query to hand them to another
    tenant.

    ``organization_id`` is denormalized (it is reachable via ``auth_config``) so
    every read has an org predicate available without a join, and so the table
    is ready to carry a row-level-security policy later.
    """

    __tablename__ = "auth_config_operations"

    auth_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_configs.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_operation_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True)
    # Always present here: a discovered operation is only reachable through its
    # kind's executor, which is driven entirely by this descriptor.
    execution: Mapped[dict] = mapped_column(JSONB, nullable=False)

    auth_config: Mapped["AuthConfig"] = relationship("AuthConfig")

    __table_args__ = (
        # Plain (not partial) uniqueness, so re-discovery can upsert with
        # ON CONFLICT instead of the delete-then-insert that loses every
        # operation when one insert collides.
        Index(
            "uq_auth_config_operations_name",
            "auth_config_id",
            "name",
            unique=True,
        ),
        Index("ix_auth_config_operations_org", "organization_id"),
    )

    def to_entity(self) -> InstallOperationEntity:
        return InstallOperationEntity.model_validate(self)

    def __repr__(self) -> str:
        return (
            f"<AuthConfigOperation(auth_config_id={self.auth_config_id}, "
            f"name={self.name})>"
        )
