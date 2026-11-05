"""Identity module domain and application errors."""

from uuid import UUID

from app.core.domain.errors import DomainError


class IdentityDomainError(DomainError):
    def __init__(
        self,
        message: str,
        code: str = "IDENTITY_ERROR",
        status_code: int = 400,
    ):
        super().__init__(message, code=code, status_code=status_code)


class IdentityValidationError(IdentityDomainError):
    def __init__(self, message: str):
        super().__init__(message, code="IDENTITY_VALIDATION_ERROR", status_code=400)


class IdentityAccessDeniedError(IdentityDomainError):
    def __init__(self, message: str = "Access denied"):
        super().__init__(message, code="IDENTITY_ACCESS_DENIED", status_code=403)


class IdentityNotFoundError(IdentityDomainError):
    def __init__(self, message: str):
        super().__init__(message, code="IDENTITY_NOT_FOUND", status_code=404)


class IdentityConflictError(IdentityDomainError):
    def __init__(self, message: str, code: str = "IDENTITY_CONFLICT"):
        super().__init__(message, code=code, status_code=409)


class UserNotFoundError(IdentityNotFoundError):
    def __init__(self, message: str = "User not found"):
        super().__init__(message)
        self.code = "USER_NOT_FOUND"


class OrganizationNotFoundError(IdentityNotFoundError):
    def __init__(self, message: str = "Organization not found"):
        super().__init__(message)
        self.code = "ORGANIZATION_NOT_FOUND"


class OrganizationMemberNotFoundError(IdentityNotFoundError):
    def __init__(self, message: str = "Organization member not found"):
        super().__init__(message)
        self.code = "ORGANIZATION_MEMBER_NOT_FOUND"


class OrganizationInvitationNotFoundError(IdentityNotFoundError):
    def __init__(self, message: str = "Invitation not found"):
        super().__init__(message)
        self.code = "ORGANIZATION_INVITATION_NOT_FOUND"


class UserConflictError(IdentityConflictError):
    def __init__(self, message: str = "User already exists"):
        super().__init__(message, code="USER_CONFLICT")


class OrganizationConflictError(IdentityConflictError):
    """A conflict on an organization's globally-unique fields.

    The code distinguishes which field lost, because a caller picking its own
    name (onboarding) can retry a taken name but not a taken email domain.
    """

    NAME_TAKEN = "ORGANIZATION_NAME_CONFLICT"
    SLUG_TAKEN = "ORGANIZATION_SLUG_CONFLICT"
    DOMAIN_TAKEN = "ORGANIZATION_DOMAIN_CONFLICT"
    LAST_OWNER = "ORGANIZATION_LAST_OWNER"

    def __init__(self, message: str, code: str = "ORGANIZATION_CONFLICT"):
        super().__init__(message, code=code)


class OrganizationMemberLimitError(DomainError):
    """The organization holds as many people as its plan allows.

    Pending invitations count: an invitation is a promise of a seat, and one
    that could not be honoured when accepted is worse than one never sent.
    """

    def __init__(self, *, limit: int, used: int):
        super().__init__(
            f"This organization's plan allows {limit} people, counting pending "
            f"invitations, and it has {used}. Upgrade to add more.",
            code="MEMBER_LIMIT_REACHED",
            status_code=403,
            details={"limit": limit, "used": used},
        )


class OrganizationLimitError(DomainError):
    """The person already owns as many organizations as their plan allows."""

    def __init__(self, *, limit: int, used: int):
        super().__init__(
            f"Your plan allows {limit} organizations, and you own {used}. "
            "Upgrade to make more.",
            code="ORGANIZATION_LIMIT_REACHED",
            status_code=403,
            details={"limit": limit, "used": used},
        )


_SIGNUP_INVITE_ONLY = "SIGNUP_INVITE_ONLY"
_SIGNUP_INVITE_ONLY_MESSAGE = (
    "This Lemma is invite-only. Ask someone already on it for an invitation."
)
_SIGNUP_CLOSED_MESSAGE = "This Lemma is not accepting new accounts."


class SignupNotAllowedError(IdentityDomainError):
    """This installation does not accept a new account from this address.

    Raised by every path that creates a user, so the refusal reads the same
    whether somebody used a password, a Google account or an emailed code. The
    codes are distinct because the two refusals ask for different things of
    the person reading them: invite-only has a way in (be invited), closed has
    none.

    The message is the whole of what the auth screen prints -- SuperTokens
    carries it as the `reason` of a `*_NOT_ALLOWED` answer -- so it has to be
    something a stranger at the sign-up page can act on, not an API status.
    """

    INVITE_ONLY = _SIGNUP_INVITE_ONLY
    CLOSED = "SIGNUP_CLOSED"

    INVITE_ONLY_MESSAGE = _SIGNUP_INVITE_ONLY_MESSAGE
    CLOSED_MESSAGE = _SIGNUP_CLOSED_MESSAGE

    def __init__(self, code: str):
        message = (
            _SIGNUP_INVITE_ONLY_MESSAGE
            if code == _SIGNUP_INVITE_ONLY
            else _SIGNUP_CLOSED_MESSAGE
        )
        super().__init__(message, code=code, status_code=403)


class AccountDeletionConfirmationError(IdentityDomainError):
    """The address typed back is not this account's.

    Deleting an account cannot be undone, so the request carries the account's
    own email address as proof the person meant this account and read what it
    does. A mismatch is refused before anything is read about the account.
    """

    def __init__(self) -> None:
        super().__init__(
            "Type this account's email address exactly to confirm deleting it.",
            code="ACCOUNT_DELETION_CONFIRMATION_MISMATCH",
            status_code=400,
        )


class AccountDeletionSoleOwnerError(IdentityConflictError):
    """The person is the only owner of organizations other people are still in.

    Deleting the account would leave each with no owner, and nobody can make
    one: every path that grants ownership needs an owner to walk it. So the
    person is asked to hand ownership on first, and told where.
    """

    def __init__(self, organizations: list[tuple[UUID, str]]) -> None:
        names = [name for _, name in organizations]
        listed = (
            names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        )
        super().__init__(
            f"Make someone else an owner of {listed} first. "
            "Other people still work there, and it cannot be left without an owner.",
            code="ACCOUNT_DELETION_SOLE_OWNER",
        )
        self.details = {
            "organizations": [
                {"id": str(organization_id), "name": name}
                for organization_id, name in organizations
            ]
        }


class AccountDeletionBlockedByBillingError(IdentityConflictError):
    """Something the person pays for has to end before the account can.

    The code and message are the billing provider's, passed through, so what
    the person reads names the actual subscription and what to do about it.
    """

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message, code=code)


class AccountDeletionUnavailableError(IdentityDomainError):
    """This deployment bills, and cannot yet say whether deleting is safe.

    Raised when plans are configured but no billing guard is: deleting anyway
    could leave a subscription charging an account that no longer exists.
    """

    def __init__(self) -> None:
        super().__init__(
            "Account deletion cannot check this account's subscription right "
            "now. Contact support and we will delete it for you.",
            code="ACCOUNT_DELETION_UNAVAILABLE",
            status_code=503,
        )
