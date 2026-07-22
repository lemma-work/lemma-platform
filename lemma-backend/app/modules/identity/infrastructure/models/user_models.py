from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.identity.domain.user_entities import UserEntity


class User(UUIDAuditBase):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deactivation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mobile_number: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    mobile_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    telegram_username: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Typed UserPreferences blob (per-user surface defaults, etc.); nullable.
    preferences: Mapped[dict | None] = mapped_column(JSONB, default=None, nullable=True)

    __table_args__ = (
        Index("uq_users_email_lower", func.lower(email), unique=True),
        Index(
            "uq_users_verified_mobile_e164",
            mobile_number,
            unique=True,
            postgresql_where=text("mobile_verified_at IS NOT NULL"),
        ),
    )

    def to_entity(self) -> UserEntity:
        return UserEntity.model_validate(self)

    def __str__(self) -> str:
        if self.first_name or self.last_name:
            name = " ".join(part for part in [self.first_name, self.last_name] if part)
            return f"{name} <{self.email}>"
        return self.email
