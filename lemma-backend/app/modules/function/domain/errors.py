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


class FunctionAccessDeniedError(FunctionDomainError):
    def __init__(self, message: str = "Access denied"):
        super().__init__(
            message=message,
            code="FUNCTION_ACCESS_DENIED",
            status_code=403,
        )


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


class FunctionResourceAccessError(FunctionAccessDeniedError):
    """Base error for function resource access violations."""

    def __init__(self, message: str, resource_type: str, resource_name: str):
        super().__init__(message=message)
        self.code = "FUNCTION_RESOURCE_ACCESS_DENIED"
        self.resource_type = resource_type
        self.resource_name = resource_name


class FunctionDatastoreAccessDeniedError(FunctionResourceAccessError):
    def __init__(self, datastore_name: str, function_name: str):
        message = (
            f"Function '{function_name}' does not have access to datastore '{datastore_name}'"
        )
        super().__init__(message, "datastore", datastore_name)
        self.function_name = function_name


class FunctionFileScopeAccessDeniedError(FunctionResourceAccessError):
    def __init__(self, datastore_name: str, function_name: str):
        message = (
            f"Function '{function_name}' does not have access to datastore file scope "
            f"'{datastore_name}'"
        )
        super().__init__(message, "file", datastore_name)
        self.function_name = function_name


class FunctionConnectorAccessDeniedError(FunctionResourceAccessError):
    def __init__(self, app_name: str, function_name: str, mode: str | None = None):
        message = (
            f"Function '{function_name}' does not have access to connector '{app_name}'"
        )
        if mode:
            message += f" in {mode} mode"
        super().__init__(message, "connector", app_name)
        self.function_name = function_name
        self.mode = mode


class FunctionResourceNotFoundError(FunctionDomainError):
    def __init__(self, resource_type: str, resource_name: str, pod_id: str):
        message = f"{resource_type.title()} '{resource_name}' not found in pod '{pod_id}'"
        super().__init__(
            message=message,
            code="FUNCTION_RESOURCE_NOT_FOUND",
            status_code=404,
        )
        self.resource_type = resource_type
        self.resource_name = resource_name
        self.pod_id = pod_id


class FunctionInvalidResourceConfigurationError(FunctionValidationError):
    def __init__(self, message: str, field: str | None = None, value: str | None = None):
        super().__init__(
            message=message,
            code="FUNCTION_INVALID_RESOURCE_CONFIG",
        )
        self.field = field
        self.value = value


class FunctionInvalidConnectorModeError(FunctionInvalidResourceConfigurationError):
    def __init__(self, mode: str, valid_modes: list[str]):
        message = (
            f"Invalid connector mode '{mode}'. "
            f"Valid modes are: {', '.join(valid_modes)}"
        )
        super().__init__(message, "mode", mode)
        self.valid_modes = valid_modes


class FunctionMissingAccountIdError(FunctionInvalidResourceConfigurationError):
    def __init__(self, app_name: str):
        message = f"Connector '{app_name}' in FIXED mode requires account_id"
        super().__init__(message, "account_id", None)
        self.app_name = app_name


class FunctionAccountOwnershipError(FunctionAccessDeniedError):
    def __init__(self, account_id: str, user_id: str):
        message = f"Account '{account_id}' is not owned by user '{user_id}'"
        super().__init__(message=message)
        self.code = "FUNCTION_ACCOUNT_OWNERSHIP_VIOLATION"
        self.account_id = account_id
        self.user_id = user_id


# Backward-compatible alias
FunctionError = FunctionDomainError
