"""App module domain errors."""

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


class AppConflictError(AppDomainError):
    def __init__(self, message: str):
        super().__init__(message=message, code="APP_CONFLICT", status_code=409)
