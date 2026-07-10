"""Framework-agnostic domain errors."""


class DomainError(Exception):
    """Base class for domain/application errors.

    Domain errors are raised from services/domain logic and mapped to transport
    concerns (e.g. HTTP status codes) at the API boundary.
    """

    def __init__(
        self,
        message: str,
        code: str = "DOMAIN_ERROR",
        status_code: int = 400,
        details: object | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details

    def __str__(self) -> str:
        return self.message


class BadRequestError(DomainError):
    """A malformed request the client can fix (bad page token, unparseable id).

    Raised at the transport boundary for value-parsing failures that would
    otherwise surface as a bare ``ValueError`` (→ 500). Auto-translates to 400
    via the global handler, so controllers don't need to catch and re-raise.
    """

    def __init__(
        self,
        message: str = "Bad request",
        *,
        code: str = "BAD_REQUEST",
        details: object | None = None,
    ):
        super().__init__(message, code=code, status_code=400, details=details)


class ValidationError(DomainError):
    """A syntactically valid request whose structured values are invalid."""

    def __init__(
        self,
        message: str = "Request validation failed",
        *,
        code: str = "VALIDATION_ERROR",
        details: object | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=422, details=details)


class PayloadTooLargeError(DomainError):
    """A request or multipart field exceeded its configured byte budget."""

    def __init__(self, *, max_bytes: int, field: str = "request") -> None:
        super().__init__(
            f"{field} exceeds the maximum allowed size",
            code="UPLOAD_TOO_LARGE",
            status_code=413,
            details={"field": field, "max_bytes": max_bytes},
        )


class UploadCapacityExceededError(DomainError):
    """This process has reached its bounded concurrent staging capacity."""

    def __init__(self) -> None:
        super().__init__(
            "Upload staging capacity is temporarily exhausted",
            code="UPLOAD_CAPACITY_EXCEEDED",
            status_code=503,
        )
