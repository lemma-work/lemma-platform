"""Private onboarding survives retries without storing auth inputs in a pod."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class VerifiedSurfaceIdentity(UUIDAuditBase):
    """Who someone is on a platform, and where their private chat goes.

    One row per binding -- platform, tenant, installation and actor, hashed --
    holding both halves of the same fact: that this person proved who they are,
    and which pod and agent they reached by doing so. The row existing *is* the
    proof; it is only written after a code sent to their mailbox came back.

    The destination used to be a second table keyed on the same binding, which
    meant two rows that could disagree: nothing stopped a live route sitting
    beside a revoked identity, and every read had to re-join and re-check
    ``revoked_at`` by hand to notice. ``ck_surface_identity_route_is_live``
    makes that state unrepresentable instead -- a revoked identity cannot carry
    a destination, so a stale route is a row the database refuses rather than a
    condition someone has to remember to write.
    """

    __tablename__ = "surface_verified_identities"
    __table_args__ = (
        CheckConstraint(
            "revoked_at IS NULL OR ("
            "installation_surface_id IS NULL AND pod_id IS NULL "
            "AND assistant_id IS NULL)",
            name="ck_surface_identity_route_is_live",
        ),
    )

    binding_key: Mapped[str] = mapped_column(String(64), unique=True)
    platform: Mapped[str] = mapped_column(String(32))
    tenant_id: Mapped[str] = mapped_column(String(255))
    external_user_id: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    verified_phone: Mapped[str | None] = mapped_column(String(32))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Where this identity talks, once it has somewhere to talk. Null until a
    # workspace is chosen or provisioned -- being recognised and having a
    # destination are different things, and the gap between them is a real
    # state a person can sit in.
    installation_surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="CASCADE")
    )
    pod_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE")
    )
    assistant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE")
    )

    @property
    def is_routable(self) -> bool:
        """Live, and with somewhere to send a message."""
        return self.revoked_at is None and self.pod_id is not None


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
    offered_pods: Mapped[list[dict[str, JsonValue]] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
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
