from datetime import date, datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.core.api.schemas import BaseSchema
from app.core.helpers.identifiers import normalize_mobile_e164


class UserProfileRequest(BaseSchema):
    """User profile request schema."""

    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
    telegram_username: str | None = Field(
        default=None,
        deprecated=True,
        description=(
            "Deprecated: accepted and ignored, and will be removed. A Telegram "
            "account is linked by sharing a contact with a Lemma bot, never by "
            "a handle typed onto a profile."
        ),
    )
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
    country: str | None = None
    timezone: str | None = None
    date_of_birth: date | None = None
    created_at: datetime
    updated_at: datetime


class AccountDeletionRequest(BaseSchema):
    """Proof that the person means this account, typed back by them."""

    # Unbounded on purpose: an empty box or an overlong paste is a mismatch
    # like any other typo, answered with the code clients branch on, not 422.
    confirm_email: str = Field(
        description=(
            "The account's own email address, typed by the person. Compared "
            "case-insensitively; anything else is refused with "
            "ACCOUNT_DELETION_CONFIRMATION_MISMATCH."
        ),
    )


class AccountDeletionResponse(BaseSchema):
    """What deleting the account did.

    Every session has ended by the time it is read -- or, if the sign-in
    service could not be reached just then, ends as soon as it can be.
    """

    deleted: bool = Field(
        description="Always true: the account no longer exists, whether this "
        "request deleted it or an earlier one had."
    )
    organizations_left: int = Field(
        description="Organizations others are still in, which the person left; "
        'their shared work stays there under "Deleted user".'
    )
    organizations_deleted: int = Field(
        description="Organizations the person was the only member of, deleted "
        "with their pods."
    )
    pods_deleted: int
