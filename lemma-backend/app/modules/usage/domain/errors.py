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
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class UsageLimitExceededError(UsageDomainError):
    """System-profile usage limit has been reached."""

    def __init__(
        self, message: str = "LLM usage limit exceeded for this account"
    ) -> None:
        super().__init__(
            message,
            code="USAGE_LIMIT_EXCEEDED",
            status_code=429,
        )


class UsageContextMissingError(UsageDomainError):
    """A metered system-profile model call did not have agent usage context."""

    def __init__(
        self, message: str = "Usage context is required for system models"
    ) -> None:
        super().__init__(
            message,
            code="USAGE_CONTEXT_MISSING",
            status_code=500,
        )


class UsageAccessDeniedError(UsageDomainError):
    """User does not have permission to view an organization's usage."""

    def __init__(self, message: str = "Access denied to usage resource") -> None:
        super().__init__(
            message,
            code="USAGE_ACCESS_DENIED",
            status_code=403,
        )


class ProviderAttemptsExhaustedError(UsageDomainError):
    """The metered boundary exhausted its provider dispatch attempts."""

    def __init__(self) -> None:
        super().__init__(
            "The model provider is temporarily unavailable after repeated attempts. Please try again later.",
            code="MODEL_PROVIDER_ATTEMPTS_EXHAUSTED",
            status_code=503,
        )


class UsageReportingError(UsageDomainError):
    """A returned provider response did not include enough usage to continue."""

    def __init__(self) -> None:
        super().__init__(
            "The model provider did not report usable usage. This execution cannot continue until its usage can be accounted for.",
            code="USAGE_REPORTING_UNAVAILABLE",
            status_code=503,
        )


class UsageCheckpointError(UsageDomainError):
    """A provider outcome could not be durably checkpointed."""

    def __init__(self) -> None:
        super().__init__(
            "Usage accounting could not be saved. The request remains recorded for reconciliation; please try again later.",
            code="USAGE_CHECKPOINT_FAILED",
            status_code=503,
        )
