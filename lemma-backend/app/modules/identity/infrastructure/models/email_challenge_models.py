"""Durable verification state, independent of browser sessions and pod access."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class EmailChallenge(UUIDAuditBase):
    __tablename__ = "identity_email_challenges"
    __table_args__ = (
        CheckConstraint(
            "attempts >= 0 AND attempts <= 3", name="ck_email_challenge_attempts"
        ),
    )

    email: Mapped[str] = mapped_column(String(255), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    binding_hash: Mapped[str] = mapped_column(String(64), index=True)
    pre_auth_session_id: Mapped[str] = mapped_column(String(255))
    code_id: Mapped[str] = mapped_column(String(255))
    device_id: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_user_id: Mapped[UUID | None]
