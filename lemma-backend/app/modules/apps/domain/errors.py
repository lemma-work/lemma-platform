"""App module domain errors."""

from uuid import UUID

from app.core.domain.errors import DomainError


class AppDomainError(DomainError):
    def __init__(self, message: str, code: str = "APP_ERROR", status_code: int = 400):
        super().__init__(message=message, code=code, status_code=status_code)


class AppValidationError(AppDomainError):
    def __init__(self, message: str):
        super().__init__(message=message, code="APP_VALIDATION_ERROR", status_code=422)


class AppAccessDeniedError(AppDomainError):
    def __init__(self, message: str = "Access denied"):
        super().__init__(message=message, code="APP_ACCESS_DENIED", status_code=403)


class AppNotFoundError(AppDomainError):
    def __init__(self, message: str = "App not found"):
        super().__init__(message=message, code="APP_NOT_FOUND", status_code=404)


class AppAssetNotFoundError(AppNotFoundError):
    """A path the app itself does not serve.

    Carries where the request was aimed, because the answer a person needs is
    not "404" -- it is what to do instead. An app whose markdown links to a pod
    file lands here, and the pod is the only thing that makes the alternative
    offer possible.
    """

    def __init__(self, message: str, *, pod_id: UUID | None, asset_path: str):
        super().__init__(message=message)
        self.pod_id = pod_id
        self.asset_path = asset_path


class AppConflictError(AppDomainError):
    def __init__(self, message: str):
        super().__init__(message=message, code="APP_CONFLICT", status_code=409)


class AppReleaseNotFoundError(AppDomainError):
    def __init__(self, message: str = "App release not found"):
        super().__init__(message=message, code="APP_RELEASE_NOT_FOUND", status_code=404)


class AppReleasePrunedError(AppDomainError):
    """A release whose bytes retention has deleted.

    Distinct from "not found": the release existed and the history still lists
    it, so saying so beats a bare 404 that reads like a typo in the version.
    """

    def __init__(self, message: str = "This release's build has been removed"):
        super().__init__(message=message, code="APP_RELEASE_PRUNED", status_code=410)
