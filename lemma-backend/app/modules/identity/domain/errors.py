"""Identity module domain and application errors."""

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
    LAST_OWNER = "ORGANIZATION_LAST_OWNER"

    def __init__(self, message: str, code: str = "ORGANIZATION_CONFLICT"):
        super().__init__(message, code=code)
