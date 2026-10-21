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


class DatastoreSignedLinkLimitError(DatastoreDomainError):
    """This person already has as many live share links as they may have.

    429 rather than 403: nothing is forbidden, there is simply no slot right now,
    and the condition clears on its own as links expire. The details say what the
    limit is and how many are live, because the caller — often an agent — has to
    decide between revoking one and waiting.

    ``live`` can read slightly above ``limit`` after a simultaneous burst; the
    limit is a bound on abuse rather than an exactly enforced quota, and
    ``SignedLinkRepository.create_within_allowance`` says why.
    """

    def __init__(self, *, limit: int, live: int):
        super().__init__(
            (
                f"You already have {live} live share links in this pod, and the "
                f"limit is {limit}. Revoke one you no longer need, or wait for "
                f"one to expire."
            ),
            code="DATASTORE_SIGNED_LINK_LIMIT",
            status_code=429,
            details={"limit": limit, "live": live},
        )


class DatastoreRetryTooSoonError(DatastoreDomainError):
    """This document was asked to be processed again too recently.

    429: nothing is wrong and the request is not refused for good, it is early.
    The details say how long the cooldown is, because the caller -- often an
    agent -- has to decide whether to wait.
    """

    def __init__(self, cooldown_seconds: int):
        super().__init__(
            "This document was retried moments ago; wait before retrying it again.",
            code="DATASTORE_RETRY_TOO_SOON",
            status_code=429,
            details={"cooldown_seconds": cooldown_seconds},
        )


class DatastoreRevocationIncompleteError(DatastoreDomainError):
    """The link is revoked in the record but still cached, so still openable.

    503 rather than 500: the durable half succeeded and nothing is corrupt —
    the cache simply could not be reached — and repeating the call finishes the
    job. Reporting plain success instead told the caller a link was dead while
    it was still serving bytes.
    """

    def __init__(self) -> None:
        super().__init__(
            "The link is revoked but its cached copy could not be dropped; "
            "retry to finish revoking it.",
            code="DATASTORE_REVOCATION_INCOMPLETE",
            status_code=503,
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
    """The extractor is down -- the document was never judged.

    Raised only with evidence that the dependency is down for everyone: the
    connection was refused, its health check failed, or the circuit is open
    because *other* documents failed. A 5xx, timeout or 413 on one document
    while the extractor serves others is not this; it is the document's
    failure and is charged an attempt (``ExtractorFailedOnDocument``).

    The distinction is the whole rule. Classing every 5xx as "unavailable" is
    how one PDF the extractor panicked on was re-processed once a minute
    forever: the attempt was refunded each time, so no budget ever ran out.

    ``evidence`` names what was observed, in the work runtime's vocabulary
    (``connection_refused``, ``health_check_failing``, ``circuit_open``,
    ``rate_limited``), so processing can say why it is waiting.
    """

    evidence: str = "connection_refused"

    def __init__(
        self,
        message: str = "Document extractor unavailable",
        *,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ExtractorFailedOnDocument(RuntimeError):
    """The extractor answered, and failed on this document.

    A 5xx, a timeout or a 413 while its health check passes: the document is
    at fault, so an attempt is charged and the failure counts once against
    this document in the circuit breaker.
    """
