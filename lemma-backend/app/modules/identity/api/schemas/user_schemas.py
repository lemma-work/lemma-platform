from datetime import date, datetime
from uuid import UUID

from pydantic import EmailStr

from app.core.api.schemas import BaseSchema


class UserProfileRequest(BaseSchema):
    """User profile request schema."""

    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
    telegram_username: str | None = None
    country: str | None = None
    timezone: str | None = None
    date_of_birth: date | None = None


class UserResponse(BaseSchema):
    """User response schema."""

    id: UUID
    email: EmailStr
    is_verified: bool
    is_active: bool
    is_superuser: bool
    first_name: str | None = None
    last_name: str | None = None
    mobile_number: str | None = None
    telegram_username: str | None = None
    country: str | None = None
    timezone: str | None = None
    date_of_birth: date | None = None
    created_at: datetime
    updated_at: datetime
