from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID
from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.identity.domain.organization_entities import (
    OrganizationRole,
    OrganizationEntity,
    OrganizationInvitationStatus,
    OrganizationJoinPolicy,
    OrganizationMemberEntity,
    OrganizationInvitationEntity,
)

if TYPE_CHECKING:
    from app.modules.identity.infrastructure.models import User


class Organization(UUIDAuditBase):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email_domain: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    join_policy: Mapped[str] = mapped_column(
        String(50),
        default=OrganizationJoinPolicy.INVITE_ONLY.value,
        nullable=False,
    )

    members: Mapped[list[OrganizationMember]] = relationship(
        "OrganizationMember",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    invitations: Mapped[list[OrganizationInvitation]] = relationship(
        "OrganizationInvitation",
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    def __str__(self) -> str:
        return self.name or str(self.id)

    def to_entity(self) -> OrganizationEntity:
        return OrganizationEntity.model_validate(self)


class OrganizationMember(UUIDAuditBase):
    __tablename__ = "organization_members"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    role: Mapped[OrganizationRole] = mapped_column(String(50))

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="members"
    )
    user: Mapped[User] = relationship("User")

    __table_args__ = (
        Index("ix_org_member_user_org", "user_id", "organization_id", unique=True),
        Index("ix_org_member_org_role", "organization_id", "role"),
    )

    def __str__(self) -> str:
        # Guard against async lazy-load: only read `user` if already loaded.
        from sqlalchemy import inspect as _sa_inspect

        if "user" not in _sa_inspect(self).unloaded and self.user is not None:
            return f"{self.user.email} ({self.role})"
        return f"member ({self.role})"

    def to_entity(self) -> OrganizationMemberEntity:
        entity = OrganizationMemberEntity.model_validate(self)
        if self.user:
            entity.user = self.user.to_entity()
        return entity


class OrganizationInvitation(UUIDAuditBase):
    __tablename__ = "organization_invitations"

    email: Mapped[str] = mapped_column(String(255), index=True)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    role: Mapped[OrganizationRole] = mapped_column(String(50))
    pod_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pods.id", ondelete="SET NULL"), nullable=True
    )
    pod_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    redirect_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[OrganizationInvitationStatus] = mapped_column(
        String(50), default=OrganizationInvitationStatus.PENDING, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="invitations"
    )

    __table_args__ = (
        Index(
            "uq_org_invitation_pending_email_org_lower",
            func.lower(email),
            organization_id,
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    def to_entity(self) -> OrganizationInvitationEntity:
        return OrganizationInvitationEntity.model_validate(self)
