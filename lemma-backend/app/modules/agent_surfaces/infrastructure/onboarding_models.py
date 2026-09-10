"""Private onboarding survives retries without storing auth inputs in a pod."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class VerifiedSurfaceIdentity(UUIDAuditBase):
    __tablename__ = "surface_verified_identities"

    binding_key: Mapped[str] = mapped_column(String(64), unique=True)
    platform: Mapped[str] = mapped_column(String(32))
    tenant_id: Mapped[str] = mapped_column(String(255))
    external_user_id: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    verified_phone: Mapped[str | None] = mapped_column(String(32))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingChatOnboarding(UUIDAuditBase):
    __tablename__ = "surface_pending_onboarding"

    binding_key: Mapped[str] = mapped_column(String(64), unique=True)
    platform: Mapped[str] = mapped_column(String(32))
    step: Mapped[str] = mapped_column(String(32))
    challenge_id: Mapped[UUID | None]
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    installation_surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE")
    )
    verified_phone: Mapped[str | None] = mapped_column(String(32))
    destination: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    original_event: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class PersonalDMRoute(UUIDAuditBase):
    __tablename__ = "surface_personal_dm_routes"

    binding_key: Mapped[str] = mapped_column(String(64), unique=True)
    installation_surface_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    pod_id: Mapped[UUID] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"))
    assistant_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE")
    )


class OnboardingInputToken(UUIDAuditBase):
    __tablename__ = "surface_onboarding_input_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    pending_id: Mapped[UUID] = mapped_column(
        ForeignKey("surface_pending_onboarding.id", ondelete="CASCADE")
    )
    step: Mapped[str] = mapped_column(String(32))
    challenge_id: Mapped[UUID | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
