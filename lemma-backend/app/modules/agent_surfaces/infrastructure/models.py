from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
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
        )
