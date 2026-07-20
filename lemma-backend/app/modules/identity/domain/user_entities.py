from datetime import date, datetime, timezone

from pydantic import EmailStr, field_validator

from app.core.domain.aggregate import AggregateRoot
from app.core.domain.entity import Entity
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.user_preferences import UserPreferences


class AuthUserEntity(Entity):
    """Authentication user entity from auth middleware."""

    pass


class UserEntity(AggregateRoot):
    """Identity user aggregate root."""

    email: EmailStr
    is_verified: bool = False
    is_active: bool = True
    is_superuser: bool = False
    is_deleted: bool = False
    email_verified_at: datetime | None = None
    deactivated_at: datetime | None = None
    deactivation_reason: str | None = None

    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
    mobile_verified_at: datetime | None = None
    telegram_username: str | None = None
    country: str | None = None
    timezone: str | None = None
    date_of_birth: date | None = None
    preferences: UserPreferences | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> str:
        return normalize_identity_email(str(value))

    def mark_signed_up(self) -> None:
        """Emit signed-up event for welcome email processing."""
        from app.modules.identity.domain.events import UserSignedUpEvent

        self.add_event(
            UserSignedUpEvent(
                user_id=self.id,
                email=str(self.email),
                first_name=self.first_name,
            )
        )

    def update_profile(self, **data: object) -> None:
        """Apply profile updates to the user aggregate."""
        allowed = {
            "first_name",
            "last_name",
            "mobile_number",
            "telegram_username",
            "country",
            "timezone",
            "date_of_birth",
        }
        for field, value in data.items():
            if field in allowed:
                if field == "mobile_number":
                    from app.core.helpers.identifiers import normalize_mobile_digits

                    digits = normalize_mobile_digits(str(value or ""))
                    if digits != normalize_mobile_digits(self.mobile_number):
                        self.mobile_verified_at = None
                    elif self.mobile_verified_at is not None and digits:
                        # A verified number remains in its canonical E.164 form
                        # even if a profile client submits cosmetic formatting.
                        value = f"+{digits}"
                setattr(self, field, value)

    def mark_email_verified(self) -> bool:
        """Mark the first verified transition and emit the one-time welcome event."""
        first_transition = not self.is_verified
        self.is_verified = True
        self.email_verified_at = self.email_verified_at or datetime.now(timezone.utc)
        if first_transition:
            self.mark_signed_up()
        return first_transition

    def deactivate(self, reason: str) -> None:
        self.is_active = False
        self.deactivated_at = self.deactivated_at or datetime.now(timezone.utc)
        self.deactivation_reason = reason
