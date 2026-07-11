from datetime import date

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

    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
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
                setattr(self, field, value)
