"""Pod module domain/application errors."""

from app.core.domain.errors import DomainError


class PodDomainError(DomainError):
    def __init__(
        self,
        message: str,
        code: str = "POD_ERROR",
        status_code: int = 400,
    ):
        super().__init__(message, code=code, status_code=status_code)


class PodNotFoundError(PodDomainError):
    def __init__(self, message: str = "Pod not found"):
        super().__init__(message, "POD_NOT_FOUND", status_code=404)


class PodMemberNotFoundError(PodDomainError):
    def __init__(self, message: str = "Pod member not found"):
        super().__init__(message, "POD_MEMBER_NOT_FOUND", status_code=404)


class PodAccessDeniedError(PodDomainError):
    def __init__(self, message: str = "Access denied"):
        super().__init__(message, "POD_ACCESS_DENIED", status_code=403)


class PodConflictError(PodDomainError):
    def __init__(self, message: str):
        super().__init__(message, "POD_CONFLICT", status_code=409)


class PodValidationError(PodDomainError):
    def __init__(self, message: str):
        super().__init__(message, "POD_VALIDATION_ERROR", status_code=400)


class PodJoinRequestNotFoundError(PodDomainError):
    def __init__(self, message: str = "Pod join request not found"):
        super().__init__(message, "POD_JOIN_REQUEST_NOT_FOUND", status_code=404)


class PodLimitReachedError(DomainError):
    """The person already owns as many pods as their plan allows.

    403 with its own code rather than a validation error, so a client can tell
    "you may not, on this plan" from "that request was malformed" and offer the
    upgrade instead of an error.
    """

    def __init__(self, *, limit: int, used: int):
        super().__init__(
            f"Your plan allows {limit} pods, and you have {used}. "
            "Delete one, or upgrade to make more.",
            code="POD_LIMIT_REACHED",
            status_code=403,
            details={"limit": limit, "used": used},
        )
