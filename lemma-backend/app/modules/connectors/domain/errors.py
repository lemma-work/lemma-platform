"""Connector module domain/connector errors."""

from app.core.domain.errors import DomainError
from app.core.redaction import redact_value


def _safe_connector_details(details: object | None) -> dict | None:
    if not isinstance(details, dict):
        return None
    allowed = {
        key: value
        for key, value in details.items()
        if str(key).lower()
        in {"status", "status_code", "code", "error", "reason", "error_type", "upstream_status", "upstream_code"}
    }
    return redact_value(allowed) if allowed else None


class ConnectorDomainError(DomainError):
    def __init__(
        self,
        message: str,
        code: str = "CONNECTOR_ERROR",
        status_code: int = 400,
        details: object | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status_code,
            details=details,
        )


class ConnectorValidationError(ConnectorDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message=message,
            code="CONNECTOR_VALIDATION_ERROR",
            status_code=400,
            details=details,
        )


class ConnectorAccessDeniedError(ConnectorDomainError):
    def __init__(self, message: str = "Access denied", details: object | None = None):
        super().__init__(
            message=message,
            code="CONNECTOR_ACCESS_DENIED",
            status_code=403,
            details=details,
        )


class ConnectorUnauthorizedError(ConnectorDomainError):
    def __init__(self, message: str = "Unauthorized", details: object | None = None):
        super().__init__(
            message=message,
            code="CONNECTOR_UNAUTHORIZED",
            status_code=401,
            details=details,
        )


class _ConnectorNotFoundBase(ConnectorDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message=message,
            code="CONNECTOR_NOT_FOUND",
            status_code=404,
            details=details,
        )


class ConnectorConflictError(ConnectorDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message=message,
            code="CONNECTOR_CONFLICT",
            status_code=409,
            details=details,
        )


class ConnectorInfrastructureError(ConnectorDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message=message,
            code="CONNECTOR_INFRA_ERROR",
            status_code=503,
            details=_safe_connector_details(details),
        )


class UnsupportedAuthProviderError(ConnectorValidationError):
    def __init__(self, provider_name: str):
        super().__init__(f"Unsupported auth provider: {provider_name}")
        self.code = "UNSUPPORTED_AUTH_PROVIDER"


class ConnectorNotFoundError(_ConnectorNotFoundBase):
    def __init__(self, connector_id: str):
        super().__init__(f"Connector '{connector_id}' not found")
        self.code = "CONNECTOR_NOT_FOUND"


class ConnectorTriggerNotFoundError(ConnectorNotFoundError):
    def __init__(self, trigger_id: str):
        super().__init__(f"Trigger '{trigger_id}' not found")
        self.code = "CONNECTOR_TRIGGER_NOT_FOUND"


class AccountNotFoundError(ConnectorNotFoundError):
    def __init__(self, account_id: str):
        super().__init__(f"Account '{account_id}' not found")
        self.code = "ACCOUNT_NOT_FOUND"


class CredentialsNotFoundError(ConnectorNotFoundError):
    def __init__(self, account_id: str):
        super().__init__(f"Credentials not found for account '{account_id}'")
        self.code = "ACCOUNT_CREDENTIALS_NOT_FOUND"


class AccountAlreadyConnectedError(ConnectorConflictError):
    def __init__(self, connector_id: str):
        super().__init__(
            f"Account already connected for connector '{connector_id}'"
        )
        self.code = "ACCOUNT_ALREADY_CONNECTED"


class ConnectRequestNotFoundError(ConnectorNotFoundError):
    def __init__(self):
        super().__init__("No pending connect request found for the provided state")
        self.code = "CONNECT_REQUEST_NOT_FOUND"


class ConnectRequestStateRequiredError(ConnectorValidationError):
    def __init__(self):
        super().__init__("State parameter is required")
        self.code = "CONNECT_REQUEST_STATE_REQUIRED"


class OAuthWorkflowError(ConnectorValidationError):
    def __init__(self, message: str, details: object | None = None):
        ConnectorDomainError.__init__(
            self,
            message=message,
            code="OAUTH_FLOW_ERROR",
            status_code=502,
            details=_safe_connector_details(details),
        )
        self.code = "OAUTH_FLOW_ERROR"


class PodConnectorNotFoundError(ConnectorNotFoundError):
    def __init__(self, alias: str):
        super().__init__(f"Pod connector '{alias}' not found")
        self.code = "POD_CONNECTOR_NOT_FOUND"


class PodConnectorConflictError(ConnectorConflictError):
    def __init__(self, alias: str):
        super().__init__(
            f"Connector with alias '{alias}' is already installed in this pod"
        )
        self.code = "POD_CONNECTOR_CONFLICT"


class PodAccountNotFoundError(ConnectorNotFoundError):
    def __init__(self):
        super().__init__("Account not found or access denied")
        self.code = "POD_ACCOUNT_NOT_FOUND"


class OperationNotFoundError(ConnectorNotFoundError):
    def __init__(self, operation_name: str):
        super().__init__(f"Operation '{operation_name}' not found")
        self.code = "OPERATION_NOT_FOUND"


class AccountResolutionError(ConnectorValidationError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(message, details=details)
        self.code = "ACCOUNT_RESOLUTION_ERROR"


class OperationExecutionError(ConnectorDomainError):
    def __init__(
        self,
        message: str,
        code: str = "OPERATION_EXECUTION_ERROR",
        status_code: int = 500,
        details: object | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status_code,
            details=details,
        )


class OperationExecutionTimeoutError(OperationExecutionError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message="Connector operation timed out.",
            code="OPERATION_EXECUTION_TIMEOUT",
            status_code=504,
            details=_safe_connector_details(details),
        )


class OperationExecutionValidationError(OperationExecutionError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message="Connector rejected the operation request.",
            code="OPERATION_EXECUTION_VALIDATION_ERROR",
            status_code=422,
            details=_safe_connector_details(details),
        )


class OperationExecutionUnauthorizedError(OperationExecutionError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message="Connector account authorization failed.",
            code="OPERATION_EXECUTION_UNAUTHORIZED",
            status_code=401,
            details=_safe_connector_details(details),
        )


class OperationExecutionAccessDeniedError(OperationExecutionError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message="Connector operation access denied.",
            code="OPERATION_EXECUTION_ACCESS_DENIED",
            status_code=403,
            details=_safe_connector_details(details),
        )


class OperationExecutionNotFoundError(OperationExecutionError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message="Connector operation was not found by the provider.",
            code="OPERATION_EXECUTION_NOT_FOUND",
            status_code=404,
            details=_safe_connector_details(details),
        )


class OperationExecutionInfrastructureError(OperationExecutionError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message="Connector provider is temporarily unavailable.",
            code="OPERATION_EXECUTION_INFRA_ERROR",
            status_code=503,
            details=_safe_connector_details(details),
        )
