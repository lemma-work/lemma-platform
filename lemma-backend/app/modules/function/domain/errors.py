"""Domain errors for function module."""

from app.core.domain.errors import DomainError


class FunctionDomainError(DomainError):
    """Base error for function module."""

    def __init__(
        self,
        message: str,
        code: str = "FUNCTION_ERROR",
        status_code: int = 400,
        details: object | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status_code,
            details=details,
        )


class FunctionValidationError(FunctionDomainError):
    def __init__(
        self,
        message: str,
        validation_errors: list[str] | None = None,
        code: str = "FUNCTION_VALIDATION_ERROR",
        details: object | None = None,
    ):
        merged_details = details
        if validation_errors:
            base = details if isinstance(details, dict) else {}
            merged_details = {
                **base,
                "validation_errors": validation_errors,
            }
        super().__init__(
            message=message,
            code=code,
            status_code=400,
            details=merged_details,
        )
        self.validation_errors = validation_errors or []


class FunctionNotFoundError(FunctionDomainError):
    def __init__(self, message: str = "Function not found"):
        super().__init__(
            message=message,
            code="FUNCTION_NOT_FOUND",
            status_code=404,
        )


class FunctionRunNotFoundError(FunctionDomainError):
    def __init__(self, message: str = "Function run not found"):
        super().__init__(
            message=message,
            code="FUNCTION_RUN_NOT_FOUND",
            status_code=404,
        )


class FunctionRevisionNotFoundError(FunctionDomainError):
    def __init__(self, message: str = "Function revision not found"):
        super().__init__(
            message=message,
            code="FUNCTION_REVISION_NOT_FOUND",
            status_code=404,
        )


class FunctionRevisionPrunedError(FunctionDomainError):
    """A revision whose artifact retention has deleted.

    Distinct from "not found": the revision existed and the history still lists
    it, so saying so beats a 404 that reads like a mistyped version.
    """

    def __init__(self, message: str = "This revision's build has been removed"):
        super().__init__(
            message=message,
            code="FUNCTION_REVISION_PRUNED",
            status_code=410,
        )


class FunctionRunQueueUnavailable(FunctionDomainError):
    """The durable function-run queue could not confirm publication."""

    def __init__(self, message: str = "Function run queue is unavailable"):
        super().__init__(
            message=message,
            code="FUNCTION_RUN_QUEUE_UNAVAILABLE",
            status_code=503,
        )


class FunctionConflictError(FunctionDomainError):
    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="FUNCTION_CONFLICT",
            status_code=409,
        )


# Backward-compatible alias
FunctionError = FunctionDomainError
