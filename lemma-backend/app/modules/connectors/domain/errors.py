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
        in {
            "status",
            "status_code",
            "code",
            "error",
            "reason",
            "error_type",
            "upstream_status",
            "upstream_code",
            # Which connector and operation, and when to come back. The circuit
            # breaker builds both and they were dropped here, so a caller got
            # `details: null` and a message naming nothing at all.
            "scope",
            "connector_id",
            "operation_name",
            "retry_after",
        }
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
    """Base for every 404 in this module.

    Each subclass writes its own whole sentence, so they extend this rather than
    ``ConnectorNotFoundError`` -- whose argument is a connector id it formats
    into a template. Subclassing that one produced messages that wrapped a
    finished sentence in another sentence ("Connector 'Operation 'x' not found'
    not found") and, worse, made every 404 in the module read to a caller as a
    missing *connector*, which is the one thing it usually was not.
    """

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


class ConnectorTriggerNotFoundError(_ConnectorNotFoundBase):
    def __init__(self, trigger_id: str):
        _ConnectorNotFoundBase.__init__(self, f"Trigger '{trigger_id}' not found")
        self.code = "CONNECTOR_TRIGGER_NOT_FOUND"


class AccountNotFoundError(_ConnectorNotFoundBase):
    def __init__(self, account_id: str):
        _ConnectorNotFoundBase.__init__(self, f"Account '{account_id}' not found")
        self.code = "ACCOUNT_NOT_FOUND"


class CredentialsNotFoundError(_ConnectorNotFoundBase):
    def __init__(self, account_id: str):
        _ConnectorNotFoundBase.__init__(
            self, f"Credentials not found for account '{account_id}'"
        )
        self.code = "ACCOUNT_CREDENTIALS_NOT_FOUND"


class AccountAlreadyConnectedError(ConnectorConflictError):
    def __init__(self, connector_id: str):
        super().__init__(f"Account already connected for connector '{connector_id}'")
        self.code = "ACCOUNT_ALREADY_CONNECTED"


class ConnectRequestNotFoundError(_ConnectorNotFoundBase):
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


class PodConnectorNotFoundError(_ConnectorNotFoundBase):
    def __init__(self, alias: str):
        super().__init__(f"Pod connector '{alias}' not found")
        self.code = "POD_CONNECTOR_NOT_FOUND"


class PodConnectorConflictError(ConnectorConflictError):
    def __init__(self, alias: str):
        super().__init__(
            f"Connector with alias '{alias}' is already installed in this pod"
        )
        self.code = "POD_CONNECTOR_CONFLICT"


class PodAccountNotFoundError(_ConnectorNotFoundBase):
    def __init__(self):
        super().__init__("Account not found or access denied")
        self.code = "POD_ACCOUNT_NOT_FOUND"


class OperationNotFoundError(_ConnectorNotFoundBase):
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


class OperationExecutionRateLimitedError(OperationExecutionError):
    """The provider asked the caller to slow down.

    Deliberately not an infrastructure error, even though it is transient: the
    provider is healthy and answering, the caller is simply asking too often.
    Counting it toward the circuit breaker would let one busy caller disable an
    operation for everyone sharing that provider, which is the opposite of what
    a rate limit is asking for. Backing off is the caller's job.
    """

    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message="Connector provider is rate limiting these requests.",
            code="OPERATION_EXECUTION_RATE_LIMITED",
            status_code=429,
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


class OperationExecutionCircuitOpenError(OperationExecutionInfrastructureError):
    """We did not call the provider, because it has been failing.

    Descends from the infrastructure error so every caller that already handles
    "provider unavailable" keeps working unchanged, but carries its own code:
    reading a log, "the provider failed" and "we stopped asking" want different
    responses, and only the second is worth retrying on a delay.

    ``OperationExecutionError`` directly rather than ``super()``: the parent
    fixes its own message and code, which is the whole thing this class exists
    to override.

    The caller's *message* is kept, which it previously was not. The breaker
    carefully builds one naming the connector and operation, and this class
    shadowed it with a fixed string -- so a caller was told "a connector is
    disabled" without being told which, and the seven of these in one
    production incident were attributable to no provider at all.
    """

    def __init__(self, message: str, details: object | None = None):
        OperationExecutionError.__init__(
            self,
            message=message
            or "Connector provider is temporarily disabled after repeated failures.",
            code="OPERATION_EXECUTION_CIRCUIT_OPEN",
            status_code=503,
            details=_safe_connector_details(details),
        )
