from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
    SurfaceCredentialMode,
    SurfaceEventMode,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
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
    # Nullable, and it stays nullable: the backfill sets it from ``updated_at``
    # for existing rows, but a row created by an older worker mid-deploy would
    # still arrive NULL. ``inbound_activity_at`` on the entity is the reader.
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
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


class NotificationModel(UUIDAuditBase):
    """Something the pod needs a person to see — see ``domain/notification.py``.

    Lives in ``agent_surfaces`` because delivery is almost entirely surface work
    (identity resolution, conversation links, platform adapters all live here).
    The agent and workflow modules reach it through ports in ``app/composition``,
    the same way they reach the scheduler.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # The inbox query: this person's notifications in this pod, newest first.
        Index(
            "ix_notifications_recipient_inbox",
            "pod_id",
            "recipient_user_id",
            "status",
            "created_at",
        ),
        # The reply path: does the conversation this inbound landed in have
        # anything open addressed to its owner?
        Index("ix_notifications_delivery_conversation", "delivery_conversation_id", "status"),
        Index("ix_notifications_origin", "origin_kind", "origin_id"),
        # The expiry sweep only ever scans OPEN rows with a deadline.
        Index(
            "ix_notifications_open_expires_at",
            "expires_at",
            postgresql_where=text("status = 'OPEN'"),
        ),
        # Pod-scoped rather than global: the key encodes a run/node id, and two
        # pods can legitimately never collide, but a global unique index would
        # make one pod's retry key a landmine for another's.
        UniqueConstraint("pod_id", "idempotency_key", name="uq_notifications_idempotency"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_pod_member_id: Mapped[UUID] = mapped_column(
        ForeignKey("pod_members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    origin_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    origin_id: Mapped[UUID | None] = mapped_column(nullable=True)
    origin_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    background_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    expects_response: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    action: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    delivery_status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    delivery_surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="SET NULL"), nullable=True
    )
    delivery_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    delivery_platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_entity(self) -> NotificationEntity:
        return NotificationEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            recipient_user_id=self.recipient_user_id,
            recipient_pod_member_id=self.recipient_pod_member_id,
            actor_user_id=self.actor_user_id,
            actor_agent_id=self.actor_agent_id,
            origin_kind=NotificationOriginKind(self.origin_kind),
            origin_id=self.origin_id,
            origin_conversation_id=self.origin_conversation_id,
            title=self.title,
            body=self.body,
            background_instruction=self.background_instruction,
            expects_response=self.expects_response,
            action=self.action,
            status=NotificationStatus(self.status),
            delivery_status=NotificationDeliveryStatus(self.delivery_status),
            delivery_surface_id=self.delivery_surface_id,
            delivery_conversation_id=self.delivery_conversation_id,
            delivery_platform=self.delivery_platform,
            delivery_error=self.delivery_error,
            response_summary=self.response_summary,
            response_data=self.response_data,
            idempotency_key=self.idempotency_key,
            expires_at=self.expires_at,
            delivered_at=self.delivered_at,
            read_at=self.read_at,
            responded_at=self.responded_at,
        )
