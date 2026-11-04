from datetime import date, datetime, timezone
from uuid import UUID

from pydantic import EmailStr, field_validator

from app.core.domain.aggregate import AggregateRoot
from app.core.domain.entity import AuthenticatedPrincipal
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.user_preferences import UserPreferences


#: Kept as an alias. The type moved to `app/core/domain/entity.py` because core
#: is what constructs it -- see `AuthenticatedPrincipal` there.
AuthUserEntity = AuthenticatedPrincipal


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
    country: str | None = None
    timezone: str | None = None
    date_of_birth: date | None = None
    preferences: UserPreferences | None = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> str:
        return normalize_identity_email(str(value))

    def mark_signed_up(self, *, method: str | None = None) -> None:
        """Emit signed-up event for welcome email processing."""
        from app.modules.identity.domain.events import UserSignedUpEvent

        self.add_event(
            UserSignedUpEvent(
                user_id=self.id,
                email=str(self.email),
                first_name=self.first_name,
                method=method,
            )
        )

    def update_profile(self, **data: object) -> None:
        """Apply profile updates to the user aggregate."""
        allowed = {
            "first_name",
            "last_name",
            "mobile_number",
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
                        from app.modules.identity.domain.events import (
                            UserMobileChangedEvent,
                        )

                        self.add_event(UserMobileChangedEvent(user_id=self.id))
                    elif self.mobile_verified_at is not None and digits:
                        # A verified number remains in its canonical E.164 form
                        # even if a profile client submits cosmetic formatting.
                        value = f"+{digits}"
                setattr(self, field, value)

    def mark_email_verified(self, *, signup_method: str | None = None) -> bool:
        """Mark the first verified transition and emit the one-time welcome event."""
        first_transition = not self.is_verified
        self.is_verified = True
        self.email_verified_at = self.email_verified_at or datetime.now(timezone.utc)
        if first_transition:
            self.mark_signed_up(method=signup_method)
        return first_transition

    def deactivate(self, reason: str) -> None:
        self.is_active = False
        self.deactivated_at = self.deactivated_at or datetime.now(timezone.utc)
        self.deactivation_reason = reason

    def erase(self) -> None:
        """Turn the account into a tombstone that holds nothing about the person.

        The row stays, keyed by the same id, because everything the person
        wrote with others -- messages, records, files, pods -- names it, and
        a foreign key to a missing row cascades. What is left on it is what
        every author line already reads: a name, and that name is "Deleted
        user". The address is replaced by one under ``.example``, which no
        mail system delivers to, so the real one is free to sign up again.
        """
        from app.modules.identity.domain.events import (
            UserDeletedEvent,
            UserMobileChangedEvent,
        )

        if self.mobile_number is not None or self.mobile_verified_at is not None:
            # What was bound to the number -- a surface identity resolved
            # through it -- is unbound by the work this fact causes.
            self.add_event(UserMobileChangedEvent(user_id=self.id))
        self.email = deleted_user_email(self.id)
        self.first_name = DELETED_USER_FIRST_NAME
        self.last_name = DELETED_USER_LAST_NAME
        self.mobile_number = None
        self.mobile_verified_at = None
        self.country = None
        self.timezone = None
        self.date_of_birth = None
        self.preferences = None
        self.deactivate(DELETED_ACCOUNT_REASON)
        self.is_deleted = True
        self.add_event(UserDeletedEvent(user_id=self.id))


#: What a deleted account is called wherever its work is shown.
DELETED_USER_FIRST_NAME = "Deleted"
DELETED_USER_LAST_NAME = "user"
DELETED_USER_NAME = f"{DELETED_USER_FIRST_NAME} {DELETED_USER_LAST_NAME}"
DELETED_ACCOUNT_REASON = "ACCOUNT_DELETED"


def deleted_user_email(user_id: UUID) -> str:
    """The address a deleted account keeps: unique per account, never deliverable."""
    return f"deleted-{user_id}@deleted.example"
