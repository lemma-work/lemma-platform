"""Sanitized database-error mapping for datastore record operations."""

from sqlalchemy.exc import DBAPIError

from app.modules.datastore.domain.errors import (
    DatastoreQueryError,
    DatastoreQueryUnavailableError,
)
from app.modules.datastore.infrastructure.db_error_parser import (
    parse_db_error,
    raise_from_db_error,
)
from app.modules.datastore.services.table_context import TableContext


def raise_record_write_error(
    exc: DBAPIError,
    *,
    operation: str,
    ctx: TableContext | None = None,
) -> None:
    """Map a write DB error without exposing SQL parameters."""
    raise_from_db_error(
        exc,
        table_name=ctx.table_name if ctx else None,
        columns=ctx.columns if ctx else None,
        operation=operation,
    )


def raise_record_read_error(
    exc: DBAPIError,
    *,
    operation: str,
    table_name: str | None = None,
    columns: list | None = None,
) -> None:
    """Map a read DB error to a sanitized query or infrastructure error.

    Raises whatever class ``parse_db_error`` chose. It used to name only
    ``DatastoreInfrastructureError`` and collapse everything else into
    ``DatastoreQueryError``, which quietly discarded the parser's decision:
    a missing datastore query role came back as a 400 saying the person's SQL
    was wrong, when the facility simply is not provisioned (`PS-DATA-021`).
    A mapper that returns a class and a caller that ignores it is a bug waiting
    for its second case, so this honours the class rather than listing them.
    """
    unavailable = _query_facility_is_absent(exc)
    if unavailable is not None:
        raise unavailable from exc
    message, details, error_cls = parse_db_error(
        exc, table_name=table_name, columns=columns, operation=operation
    )
    if not isinstance(error_cls, type) or not issubclass(error_cls, Exception):
        error_cls = DatastoreQueryError
    if details is not None:
        raise error_cls(message, details) from exc
    raise error_cls(message) from exc


def _query_facility_is_absent(exc: Exception) -> Exception | None:
    """The deployment has no datastore query role, so no query can run.

    `SET LOCAL ROLE "<datastore_query_role>"` is the first thing ad-hoc SQL
    does, and on a deployment that never provisioned the role it is also the
    last. Recognised here rather than in ``parse_db_error`` because the message
    names no table and no column: the generic path turned it into a 400 saying
    the person's SQL was wrong, when nothing they can write will help.
    See `PS-DATA-021`.
    """
    raw = str(getattr(exc, "orig", exc)).lower()
    if 'role "' not in raw or "does not exist" not in raw:
        return None
    return DatastoreQueryUnavailableError(
        "Direct querying is not available on this deployment: its datastore "
        "query role is not provisioned. This is a deployment setting, not a "
        "problem with the query."
    )
