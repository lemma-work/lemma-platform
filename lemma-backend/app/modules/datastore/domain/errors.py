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
        super().__init__(message, code="DATASTORE_ACCESS_DENIED", status_code=403, details=details)


class DatastoreConflictError(DatastoreDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(message, code="DATASTORE_CONFLICT", status_code=409, details=details)


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


class DatastoreReservedResourceError(DatastoreDomainError):
    def __init__(self, message: str):
        super().__init__(message, code="DATASTORE_RESERVED_RESOURCE", status_code=403)


class DatastoreQueryError(DatastoreDomainError):
    def __init__(self, message: str, details: object | None = None):
        super().__init__(message, code="DATASTORE_QUERY_ERROR", status_code=400, details=details)


class DatastoreInfrastructureError(DatastoreDomainError):
    def __init__(self, message: str):
        super().__init__(message, code="DATASTORE_INFRA_ERROR", status_code=500)
