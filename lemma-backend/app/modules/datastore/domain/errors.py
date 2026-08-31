"""Datastore module domain/application errors."""

from app.core.domain.errors import DomainError


class DatastoreDomainError(DomainError):
    def __init__(
        self,
        message: str,
        code: str = "DATASTORE_ERROR",
        status_code: int = 400,
        details: object | None = None,
    ):
        super().__init__(message, code=code, status_code=status_code, details=details)


class DatastoreValidationError(DatastoreDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message, code="DATASTORE_VALIDATION_ERROR", status_code=400, details=details
        )


class DatastoreAccessDeniedError(DatastoreDomainError):
    def __init__(self, message: str = "Access denied", details: object | None = None):
        super().__init__(
            message, code="DATASTORE_ACCESS_DENIED", status_code=403, details=details
        )


class DatastoreConflictError(DatastoreDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message, code="DATASTORE_CONFLICT", status_code=409, details=details
        )


class DatastoreNotFoundError(DatastoreDomainError):
    def __init__(self, message: str = "Datastore not found"):
        super().__init__(message, code="DATASTORE_NOT_FOUND", status_code=404)


class DatastoreTableNotFoundError(DatastoreDomainError):
    def __init__(self, message: str = "Table not found"):
        super().__init__(message, code="DATASTORE_TABLE_NOT_FOUND", status_code=404)


class DatastoreRecordNotFoundError(DatastoreDomainError):
    def __init__(self, message: str = "Record not found"):
        super().__init__(message, code="DATASTORE_RECORD_NOT_FOUND", status_code=404)


class DatastoreFileNotFoundError(DatastoreDomainError):
    def __init__(self, message: str = "File not found"):
        super().__init__(message, code="DATASTORE_FILE_NOT_FOUND", status_code=404)


class DatastoreObjectNotFoundError(DatastoreDomainError):
    """The underlying storage object for a file is missing.

    Raised by the storage adapter when a blob the metadata still references has
    been deleted/never written (e.g. GCS ``NoSuchKey``). Callers translate it
    into a clean ``DatastoreFileNotFoundError`` (404) instead of letting the raw
    storage error surface as a 500.
    """

    def __init__(self, message: str = "Storage object not found"):
        super().__init__(message, code="DATASTORE_OBJECT_NOT_FOUND", status_code=404)


class DatastoreObjectIntegrityError(DatastoreDomainError):
    """Stored original bytes do not match the content accepted by the API."""

    def __init__(self, message: str = "Storage object failed integrity verification"):
        super().__init__(message, code="DATASTORE_OBJECT_INTEGRITY", status_code=500)


class DatastoreReservedResourceError(DatastoreDomainError):
    def __init__(self, message: str):
        super().__init__(message, code="DATASTORE_RESERVED_RESOURCE", status_code=403)


class DatastoreQueryError(DatastoreDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message, code="DATASTORE_QUERY_ERROR", status_code=400, details=details
        )


class DatastoreInfrastructureError(DatastoreDomainError):
    # `details` is not decoration: `parse_db_error` returns this class or
    # `DatastoreQueryError` interchangeably, and every caller then does
    # `error_cls(message, details)`. Without the parameter that call raised
    # `TypeError: __init__() takes 2 positional arguments but 3 were given`
    # -- destroying both the real error and the `from exc` chain, on the
    # database-failure path where the cause matters most.
    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message, code="DATASTORE_INFRA_ERROR", status_code=500, details=details
        )


class DatastoreQueryUnavailableError(DatastoreDomainError):
    """Direct querying is not available on this deployment.

    `PS-DATA-021`: a person who wrote perfectly good SQL must not be told their
    query was the problem. Ad-hoc SQL runs as a dedicated Postgres role
    (`datastore_query_role`), and a managed Postgres that never provisioned it
    leaves every query with nowhere to run — a fact about the deployment that
    no amount of rewriting the query will change.

    503 rather than 400 for exactly that reason, and rather than 500 because
    the platform is not broken: this one facility is absent, everything else in
    the datastore works, and an operator can fix it by granting the role.
    """

    def __init__(self, message: str, details: object | None = None):
        super().__init__(
            message,
            code="DATASTORE_QUERY_UNAVAILABLE",
            status_code=503,
            details=details,
        )


class DocumentExtractionUnavailableError(RuntimeError):
    """The extractor could not be reached — the document was never judged.

    This is the engine-neutral signal for *infrastructure* backpressure:
    connection refused, a 5xx or timeout from the extraction service, or an open
    circuit breaker. It deliberately does NOT mean "this document is bad".

    The distinction is load-bearing. ``claim_for_processing`` spends one of a
    file's ``datastore_recovery_max_attempts`` (3) on every claim, and the
    recovery cron terminally fails a file once that budget is gone. Treating an
    extractor outage as a document failure therefore turns three blips into a
    permanently-failed user document. Processing catches this specific type and
    calls ``release_claim``, which returns the row to PENDING and refunds the
    attempt, so the file is re-driven when the extractor comes back.

    Adapters raise their own subclass (e.g. ``KreuzbergTransientError``) so the
    processing service never has to know which engine is configured.
    """

    def __init__(self, message: str = "Document extractor unavailable"):
        super().__init__(message)
