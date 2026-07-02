"""Pod bundle module domain/application errors."""

from app.core.domain.errors import DomainError


class PodBundleDomainError(DomainError):
    def __init__(
        self,
        message: str,
        code: str = "POD_BUNDLE_ERROR",
        status_code: int = 400,
        details: object | None = None,
    ):
        super().__init__(message, code=code, status_code=status_code, details=details)


class BundleJobExpiredError(PodBundleDomainError):
    """The Redis state for this import/export/publish id is gone (TTL or never
    existed). The remedy is always to start over — re-upload / re-run — which
    is safe because apply is a diff against current pod state."""

    def __init__(self, message: str = "This operation has expired. Start it again to continue."):
        super().__init__(message, code="POD_BUNDLE_EXPIRED", status_code=410)


class BundleInvalidError(PodBundleDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message, code="POD_BUNDLE_INVALID", status_code=422, details=details
        )


class BundleTooLargeError(PodBundleDomainError):
    def __init__(self, message: str):
        super().__init__(message, code="POD_BUNDLE_TOO_LARGE", status_code=413)


class BundleJobConflictError(PodBundleDomainError):
    """The operation cannot run in the job's current status (e.g. apply while
    already applying, or before planning finished)."""

    def __init__(self, message: str):
        super().__init__(message, code="POD_BUNDLE_CONFLICT", status_code=409)


class BundleConfirmationRequiredError(PodBundleDomainError):
    """Destructive steps present without ``confirm_destructive``, or required
    variables missing."""

    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message, code="POD_BUNDLE_CONFIRMATION_REQUIRED", status_code=422, details=details
        )


class BundleStagingMissingError(PodBundleDomainError):
    """The staged archive was swept; replan/apply need a fresh upload."""

    def __init__(self, message: str = "The staged bundle is no longer available. Upload it again."):
        super().__init__(message, code="POD_BUNDLE_STAGING_MISSING", status_code=410)
