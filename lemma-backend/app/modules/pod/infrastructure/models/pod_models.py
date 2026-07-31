from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.identity.contracts import OrganizationRole
from app.modules.pod.domain.pod_entities import (
    PodJoinRequestEntity,
    PodJoinRequestStatus,
    PodRole,
    PodEntity,
    PodMemberEntity,
)

class Pod(UUIDAuditBase):
    __tablename__ = "pods"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    members: Mapped[list[PodMember]] = relationship(
        "PodMember",
        back_populates="pod",
        cascade="all, delete-orphan",
    )
    __table_args__ = (
        Index("ix_pod_user_name", "user_id", "name"),
        Index("ix_pod_org_name", "organization_id", "name"),
        Index(
            "uq_pod_active_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=is_deleted.is_(False),
        ),
    )

    def to_entity(self) -> PodEntity:
        return PodEntity.model_validate(self)

    def __str__(self) -> str:
        return self.name


class PodMember(UUIDAuditBase):
    __tablename__ = "pod_members"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
    )
    organization_member_id: Mapped[UUID] = mapped_column(
        ForeignKey("organization_members.id", ondelete="CASCADE"),
    )

    pod: Mapped[Pod] = relationship("Pod", back_populates="members")
    organization_member: Mapped[Any] = relationship("OrganizationMember")

    __table_args__ = (
        Index(
            "ix_pod_member_pod_org_member",
            "pod_id",
            "organization_member_id",
            unique=True,
        ),
    )

    def to_entity(self) -> PodMemberEntity:
        entity = PodMemberEntity.model_validate(self)
        if self.organization_member:
            entity.user_id = self.organization_member.user_id
            if self.organization_member.user:
                user = self.organization_member.user.to_entity()
                entity.user = user
                entity.user_email = str(user.email)
                parts = [part for part in [user.first_name, user.last_name] if part]
                entity.user_name = " ".join(parts) or None
        return entity


class PodJoinRequest(UUIDAuditBase):
    __tablename__ = "pod_join_requests"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[PodJoinRequestStatus] = mapped_column(
        String(50),
        default=PodJoinRequestStatus.PENDING,
        index=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    org_role: Mapped[OrganizationRole | None] = mapped_column(String(50), nullable=True)
    pod_role: Mapped[PodRole | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("ix_pod_join_request_pod_user_status", "pod_id", "user_id", "status"),
        Index("ix_pod_join_request_org_status", "organization_id", "status"),
    )

    def to_entity(self) -> PodJoinRequestEntity:
        return PodJoinRequestEntity.model_validate(self)
