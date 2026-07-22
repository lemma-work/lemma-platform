from datetime import date, datetime
from uuid import UUID

from pydantic import EmailStr, field_validator

from app.core.api.schemas import BaseSchema
from app.core.helpers.identifiers import normalize_mobile_e164


class UserProfileRequest(BaseSchema):
    """User profile request schema."""

    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
    telegram_username: str | None = None
    country: str | None = None
    timezone: str | None = None
    date_of_birth: date | None = None

    @field_validator("mobile_number", mode="before")
    @classmethod
    def normalize_mobile_number(cls, value: object) -> str | None:
        """Require an explicit country code and persist canonical E.164."""
        if value is None or not str(value).strip():
            return None
        return normalize_mobile_e164(str(value))


class UserResponse(BaseSchema):
    """User response schema."""

    id: UUID
    email: EmailStr
    is_verified: bool
    is_active: bool
    is_superuser: bool
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
    created_at: datetime
    updated_at: datetime
