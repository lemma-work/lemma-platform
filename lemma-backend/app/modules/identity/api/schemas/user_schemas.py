from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

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


class InstallationResponse(BaseSchema):
    """What this installation is, and what the caller is to it."""

    deployment: Literal["server", "desktop"] = Field(
        description=(
            "``desktop`` for a Lemma Desktop installation on one person's "
            "computer, ``server`` for hosted and self-hosted deployments."
        )
    )
    is_owner: bool = Field(
        description=(
            "Whether the caller is this installation's owner: the first account "
            "created on a Desktop installation. Always false on ``server``."
        )
    )
    signup_mode: Literal["open", "invite_only", "closed"] = Field(
        description="Who may create a new account on this installation."
    )
