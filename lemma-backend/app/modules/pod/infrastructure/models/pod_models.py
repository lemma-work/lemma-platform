from __future__ import annotations
from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.identity.contracts import OrganizationRole
from app.modules.pod.domain.pod_entities import (
    PodJoinRequestEntity,
    ResourceAccessInviteEntity,
    ResourceAccessInviteStatus,
    ResourceAccessRequestEntity,
    ResourceAccessRequestStatus,
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


class ResourceAccessRequest(UUIDAuditBase):
    """A request for one resource, not for pod membership.

    Mirrors ``pod_join_requests`` in shape so the two read alike in the admin UI,
    but resolves to a resource grant rather than a membership row.
    """

    __tablename__ = "resource_access_requests"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    resource_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    requested_permission_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    status: Mapped[ResourceAccessRequestStatus] = mapped_column(
        String(50),
        default=ResourceAccessRequestStatus.PENDING,
        index=True,
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        # The listing an owner opens: everything still pending on this resource.
        Index(
            "ix_resource_access_request_resource_status",
            "pod_id",
            "resource_type",
            "resource_id",
            "status",
        ),
        # "have I already asked for this?", which the guest view checks on load.
        Index(
            "ix_resource_access_request_requester",
            "requester_user_id",
            "pod_id",
            "status",
        ),
        # One live ask per person per resource, so refreshing the guest view does
        # not queue duplicates for an owner to wade through. Partial, so a
        # rejected ask can be retried later.
        Index(
            "uq_resource_access_request_pending",
            "pod_id",
            "resource_type",
            "resource_id",
            "requester_user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    def to_entity(self) -> ResourceAccessRequestEntity:
        return ResourceAccessRequestEntity.model_validate(self)


class ResourceAccessInvite(UUIDAuditBase):
    """A resource grant held against an email until an account exists for it."""

    __tablename__ = "resource_access_invites"

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"),
        index=True,
    )
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    resource_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stored normalized (lowercased/trimmed) by the service, so redemption can
    # match on equality rather than hoping both sides normalized the same way.
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    permission_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[ResourceAccessInviteStatus] = mapped_column(
        String(50),
        default=ResourceAccessInviteStatus.PENDING,
        index=True,
    )
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redeemed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # Redemption's only query: everything owed to this address.
        Index(
            "ix_resource_access_invite_email_status",
            "email",
            "status",
        ),
        # One live invite per address per resource, so re-inviting updates rather
        # than accumulating.
        Index(
            "uq_resource_access_invite_pending",
            "pod_id",
            "resource_type",
            "resource_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    def to_entity(self) -> ResourceAccessInviteEntity:
        return ResourceAccessInviteEntity.model_validate(self)
