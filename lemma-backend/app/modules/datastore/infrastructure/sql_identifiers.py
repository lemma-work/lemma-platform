"""Single source of truth for datastore SQL identifier/literal safety.

These helpers were previously duplicated across ``schema_manager``,
``record_repository``, ``datastore_repository`` and ``file_chunk_repository``.
They are intentionally dependency-light (only domain errors) so any layer can
import them without creating cycles.
"""

from __future__ import annotations

import math
from decimal import Decimal

from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreValidationError,
)


def sanitize_identifier(identifier: str) -> str:
    """Validate a SQL identifier (table/column name).

    Only alphanumeric characters and underscores are permitted. The validated
    identifier is returned unchanged; callers must still double-quote it when
    embedding into SQL. Raises :class:`DatastoreValidationError` on rejection.
    """
    if not identifier or not all(c.isalnum() or c == "_" for c in identifier):
        raise DatastoreValidationError(f"Invalid identifier: {identifier}")
    return identifier


#: PostgreSQL's identifier ceiling. Longer names are truncated *silently*, so
#: two table or column names sharing a 63-byte prefix become one object in the
#: physical schema: the second ``table.create`` answers 409 "already exists" for
#: a name ``table.list`` does not show, and nothing tells the user why.
#: ``record_indexes.py`` already defends its generated names against exactly
#: this; the user-chosen ones underneath had no rule at all.
MAX_IDENTIFIER_BYTES = 63


def ensure_identifier_fits(identifier: str, *, kind: str) -> str:
    """Reject a name PostgreSQL would truncate, naming the limit.

    Measured in encoded bytes rather than characters, which is what PostgreSQL
    counts: a non-ASCII name reaches the ceiling sooner than its length says.

    Deliberately not folded into ``sanitize_identifier``, which also runs on the
    read path -- a table created before this rule exists is already truncated in
    the physical schema and still resolves, and refusing it there would take a
    working table away rather than prevent a broken one.
    """
    encoded = len(identifier.encode("utf-8"))
    if encoded > MAX_IDENTIFIER_BYTES:
        raise DatastoreValidationError(
            f"{kind} '{identifier}' is {encoded} bytes; PostgreSQL truncates "
            f"names at {MAX_IDENTIFIER_BYTES} bytes, which would silently "
            "collide with another name sharing that prefix. Choose a shorter "
            "name."
        )
    return identifier


def escape_like(value: str) -> str:
    """Escape ``%``/``_`` wildcards for a ``LIKE ... ESCAPE '!'`` clause."""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def quote_sql_literal(value: object) -> str:
    """Render a Python value as a safe SQL literal for DDL contexts.

    DDL constructs such as ``DEFAULT`` and ``CHECK`` cannot use bind
    parameters, so values destined for them must be rendered as literals.
    Strings are single-quoted with embedded quotes doubled; booleans and
    finite numbers are emitted directly. Anything else is rejected.
    """
    # bool is a subclass of int — check it first.
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Decimal)):
        as_float = float(value)
        if math.isnan(as_float) or math.isinf(as_float):
            raise DatastoreValidationError(
                f"Non-finite numeric literal is not allowed: {value!r}"
            )
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    raise DatastoreValidationError(
        f"Unsupported SQL literal type: {type(value).__name__}"
    )


def map_datastore_db_error(
    *,
    operation: str,
    exc: Exception,
    table_name: str | None = None,
    column_name: str | None = None,
    columns: list | None = None,
) -> Exception:
    """Translate a raw DB driver error into a typed datastore domain error.

    Shared by ``SchemaManager`` (DDL) and the record repository (DML) so that
    unique/already-exists violations consistently surface as
    :class:`DatastoreConflictError` (409) rather than a generic 500/400.

    Constraint violations (check, FK, not-null, type) are mapped to
    :class:`DatastoreValidationError` (400) with clean messages and structured
    ``details`` via :mod:`db_error_parser`. Only true infrastructure failures
    (connection, timeout) remain as :class:`DatastoreInfrastructureError` (500).
    """
    raw = str(getattr(exc, "orig", exc))
    lower = raw.lower()

    if "gen_random_uuid" in lower and "does not exist" in lower:
        return DatastoreValidationError(
            "UUID auto columns require PostgreSQL UUID support. "
            "Install pgcrypto extension or use SERIAL/INTEGER primary key."
        )

    if "already exists" in lower and "table" in lower:
        if table_name:
            return DatastoreConflictError(
                f"Table '{table_name}' already exists in this datastore"
            )
        return DatastoreConflictError("Table already exists in this datastore")

    if "already exists" in lower and "column" in lower:
        if column_name:
            return DatastoreConflictError(f"Column '{column_name}' already exists")
        return DatastoreConflictError("Column already exists")

    # Use the shared parser for constraint violations + fallback.
    from app.modules.datastore.infrastructure.db_error_parser import parse_db_error

    message, details, error_cls = parse_db_error(
        exc,
        table_name=table_name,
        columns=columns,
        operation=operation,
    )
    if details is not None:
        return error_cls(message, details)
    return error_cls(message)
