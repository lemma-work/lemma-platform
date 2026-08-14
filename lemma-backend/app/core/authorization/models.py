"""SQLAlchemy models for centralized authorization."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.infrastructure.db.base import StringAuditBase, UUIDAuditBase, UUIDCreatedBase


class AuthPermissionModel(StringAuditBase):
    __tablename__ = "auth_permissions"

    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    system_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RoleModel(UUIDAuditBase):
    __tablename__ = "roles"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    pod_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    permissions: Mapped[list[RolePermissionModel]] = relationship(
        "RolePermissionModel",
        cascade="all, delete-orphan",
        # The FK already declares ON DELETE CASCADE, so the database removes
        # these rows itself. Without passive_deletes SQLAlchemy insists on
        # loading every child into the session first and deleting them one
        # at a time -- which on a large collection is a memory event, not a
        # slow query.
        passive_deletes=True,
        back_populates="role",
    )

    __table_args__ = (
        Index(
            "uq_roles_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=(pod_id.is_(None)),
        ),
        Index(
            "uq_roles_pod_name",
            "pod_id",
            "name",
            unique=True,
            postgresql_where=(pod_id.is_not(None)),
        ),
        Index("ix_roles_org_pod", "organization_id", "pod_id"),
    )


class RolePermissionModel(UUIDCreatedBase):
    __tablename__ = "role_permissions"

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_id: Mapped[str] = mapped_column(
        ForeignKey("auth_permissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    role: Mapped[RoleModel] = relationship("RoleModel", back_populates="permissions")

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
        Index("ix_role_permissions_permission", "permission_id"),
    )


class RoleAssignmentModel(UUIDCreatedBase):
    __tablename__ = "role_assignments"

    role_id: Mapped[UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    principal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    principal_id: Mapped[UUID] = mapped_column(nullable=False)
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    role: Mapped[RoleModel] = relationship("RoleModel")

    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "principal_type",
            "principal_id",
            name="uq_role_assignments_role_principal",
        ),
        Index("ix_role_assignments_principal", "principal_type", "principal_id"),
        Index("ix_role_assignments_role_id", "role_id"),
    )


class ResourcePermissionGrantModel(UUIDCreatedBase):
    __tablename__ = "resource_permission_grants"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    grantee_type: Mapped[str] = mapped_column(String(40), nullable=False)
    grantee_id: Mapped[UUID] = mapped_column(nullable=False)
    permission_id: Mapped[str] = mapped_column(
        ForeignKey("auth_permissions.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "pod_id",
            "resource_type",
            "resource_id",
            "grantee_type",
            "grantee_id",
            "permission_id",
            name="uq_resource_permission_grants_scope",
        ),
        Index("ix_resource_permission_grants_resource", "pod_id", "resource_type", "resource_id"),
        Index(
            "ix_resource_permission_grants_grantee",
            "pod_id",
            "grantee_type",
            "grantee_id",
            "permission_id",
        ),
        Index(
            "ix_rpg_resource_permission_grantee",
            "pod_id",
            "resource_type",
            "resource_id",
            "permission_id",
            "grantee_type",
            "grantee_id",
        ),
        Index(
            "ix_rpg_grantee_resource_permission",
            "pod_id",
            "grantee_type",
            "grantee_id",
            "resource_type",
            "permission_id",
            "resource_id",
        ),
    )
