from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    AgentSurfaceStatus,
    ExternalSurfaceUserEntity,
    MemberReach,
    Notification,
    NotificationOrigin,
    ReachKind,
    ReachStatus,
    SurfaceCredentialMode,
    SurfaceEventMode,
    SurfaceMode,
    SurfacePlatform,
    SurfaceTarget,
)


class AgentSurface(UUIDAuditBase):
    """External platform surface connected to a default agent or pod agent."""

    __tablename__ = "agent_surfaces"
    __table_args__ = (
        UniqueConstraint("pod_id", "name", name="uq_agent_surface_pod_name"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True
    )
    # Stable, pod-unique identifier addressed by the API (like agent names).
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), index=True, nullable=True
    )

    surface_type: Mapped[str] = mapped_column(String(50), index=True)
    mode: Mapped[str] = mapped_column(String(50), default="DM", server_default="DM", index=True)
    event_mode: Mapped[str] = mapped_column(
        String(50), default="WEBHOOK", server_default="WEBHOOK", index=True
    )
    credential_mode: Mapped[str] = mapped_column(
        String(50), default="SYSTEM", server_default="SYSTEM", index=True
    )
    config: Mapped[dict] = mapped_column(JSONB)
    account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_workspace_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    surface_identity_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    surface_identity_username: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE", server_default="ACTIVE")
    schedule_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    surface_identity_email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    # Encrypted at rest via app.core.crypto (compact ``lsenc1:`` envelope). Text
    # (not String(255)) because the envelope is longer than the raw secret.
    webhook_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_entity(self) -> AgentSurfaceEntity:
        surface_type_raw = self.surface_type or "SLACK"
        if "." in surface_type_raw:
            surface_type_raw = surface_type_raw.rsplit(".", 1)[-1]

        return AgentSurfaceEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            name=self.name or surface_type_raw.lower(),
            agent_id=self.agent_id,
            surface_type=SurfacePlatform(surface_type_raw.upper()),
            mode=SurfaceMode(self.mode or SurfaceMode.DM.value),
            event_mode=SurfaceEventMode(self.event_mode or SurfaceEventMode.WEBHOOK.value),
            credential_mode=SurfaceCredentialMode(
                self.credential_mode or SurfaceCredentialMode.SYSTEM.value
            ),
            config=self.config,
            account_id=self.account_id,
            external_workspace_id=self.external_workspace_id,
            external_tenant_id=self.external_tenant_id,
            external_channel_id=self.external_channel_id,
            surface_identity_id=self.surface_identity_id,
            surface_identity_username=self.surface_identity_username,
            status=self.status or AgentSurfaceStatus.ACTIVE.value,
            schedule_id=self.schedule_id,
            surface_identity_email=self.surface_identity_email,
            # Decrypt at rest; legacy plaintext rows pass through unchanged.
            webhook_secret=get_secret_cipher().decrypt_str(self.webhook_secret),
        )


class AgentSurfaceExternalUser(UUIDAuditBase):
    __tablename__ = "agent_surface_external_users"
    __table_args__ = (
        Index(
            "ix_agent_surface_external_user_platform_tenant_external",
            "platform",
            "tenant_id",
            "external_user_id",
            unique=True,
        ),
    )

    platform: Mapped[str] = mapped_column(String(50), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    resolved_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> ExternalSurfaceUserEntity:
        return ExternalSurfaceUserEntity.model_validate(self)


class MemberReachModel(UUIDAuditBase):
    """How a pod can reach one person on one channel."""

    __tablename__ = "member_reaches"
    __table_args__ = (
        # A person has at most one reach per channel per pod. Two indexes rather
        # than one because the APP reach has no surface, and Postgres treats
        # NULLs as distinct in a unique constraint — a single index would happily
        # admit duplicate APP rows.
        Index(
            "uq_member_reach_pod_user_kind_surface",
            "pod_id",
            "user_id",
            "kind",
            "surface_id",
            unique=True,
            postgresql_where=text("surface_id IS NOT NULL"),
        ),
        Index(
            "uq_member_reach_pod_user_kind_app",
            "pod_id",
            "user_id",
            "kind",
            unique=True,
            postgresql_where=text("surface_id IS NULL"),
        ),
        # The delivery-path query: every live reach for one person in one pod.
        Index("ix_member_reach_pod_user_status", "pod_id", "user_id", "status"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ACTIVE", server_default="ACTIVE"
    )
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opted_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> MemberReach:
        return MemberReach(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            user_id=self.user_id,
            kind=ReachKind(self.kind),
            surface_id=self.surface_id,
            external_user_id=self.external_user_id,
            target=SurfaceTarget.model_validate(self.target) if self.target else None,
            status=ReachStatus(self.status or ReachStatus.ACTIVE.value),
            last_inbound_at=self.last_inbound_at,
            window_expires_at=self.window_expires_at,
            opted_out_at=self.opted_out_at,
        )


class NotificationModel(UUIDAuditBase):
    """One thing a person is being told, in Lemma itself."""

    __tablename__ = "notifications"
    __table_args__ = (
        # The badge query, and the only one on the render hot path.
        Index(
            "ix_notification_user_unread",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_notification_pod_user_created", "pod_id", "user_id", "created_at"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    origin_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    origin_id: Mapped[UUID | None] = mapped_column(nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> Notification:
        return Notification(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            agent_id=self.agent_id,
            title=self.title,
            body=self.body,
            origin_type=(
                NotificationOrigin(self.origin_type) if self.origin_type else None
            ),
            origin_id=self.origin_id,
            read_at=self.read_at,
        )


class AgentSurfaceConversationLinkModel(UUIDAuditBase):
    __tablename__ = "agent_surface_conversation_links"
    __table_args__ = (
        Index(
            "ix_agent_surface_link_external_thread",
            "surface_id",
            "platform",
            "external_channel_id",
            "external_thread_id",
            "external_user_id",
            unique=True,
        ),
        Index("ix_agent_surface_link_conversation", "conversation_id"),
    )

    surface_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    external_channel_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    external_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routed_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conversation_kind: Mapped[str] = mapped_column(
        String(50), default="DM", server_default="DM", nullable=False
    )
    route_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_event: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # When this thread last heard from the *person*. Distinct from updated_at,
    # which a proactive send also bumps — the DM reset rule has to key off
    # inbound recency or an agent message silently suppresses the reset and
    # leaks yesterday's context into today.
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def to_entity(self) -> AgentSurfaceConversationLink:
        return AgentSurfaceConversationLink(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            surface_id=self.surface_id,
            conversation_id=self.conversation_id,
            platform=self.platform,
            external_channel_id=self.external_channel_id,
            external_thread_id=self.external_thread_id,
            external_user_id=self.external_user_id,
            routed_agent_id=self.routed_agent_id,
            conversation_kind=self.conversation_kind or "DM",
            route_key=self.route_key,
            last_event=self.last_event or {},
            last_message_id=self.last_message_id,
            last_inbound_at=self.last_inbound_at,
        )
