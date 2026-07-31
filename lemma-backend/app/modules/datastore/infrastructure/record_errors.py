"""Sanitized database-error mapping for datastore record operations."""

from sqlalchemy.exc import DBAPIError

from app.modules.datastore.domain.errors import (
    DatastoreInfrastructureError,
    DatastoreQueryError,
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
    """Map a read DB error to a sanitized query or infrastructure error."""
    message, details, error_cls = parse_db_error(
        exc, table_name=table_name, columns=columns, operation=operation
    )
    if error_cls is DatastoreInfrastructureError:
        if details is not None:
            raise DatastoreInfrastructureError(message, details) from exc
        raise DatastoreInfrastructureError(message) from exc
    if details is not None:
        raise DatastoreQueryError(message, details) from exc
    raise DatastoreQueryError(message) from exc
