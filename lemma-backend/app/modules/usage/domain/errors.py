"""Usage domain errors."""

from __future__ import annotations

from app.core.domain.errors import DomainError


class UsageDomainError(DomainError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "USAGE_ERROR",
        status_code: int = 400,
    ):
        super().__init__(message, code=code, status_code=status_code)


class UsageLimitExceededError(UsageDomainError):
    """System-profile usage limit has been reached."""

    def __init__(self, message: str = "LLM usage limit exceeded for this account"):
        super().__init__(
            message,
            code="USAGE_LIMIT_EXCEEDED",
            status_code=429,
        )


class UsageContextMissingError(UsageDomainError):
    """A metered system-profile model call did not have agent usage context."""

    def __init__(self, message: str = "Usage context is required for system models"):
        super().__init__(
            message,
            code="USAGE_CONTEXT_MISSING",
            status_code=500,
        )


class UsageAccessDeniedError(UsageDomainError):
    """User does not have permission to view an organization's usage."""

    def __init__(self, message: str = "Access denied to usage resource"):
        super().__init__(
            message,
            code="USAGE_ACCESS_DENIED",
            status_code=403,
        )
