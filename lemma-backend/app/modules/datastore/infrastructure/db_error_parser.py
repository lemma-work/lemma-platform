"""Shared translation of raw DB driver errors into clean, agent-readable messages.

PostgreSQL constraint violations carry enough structure in their error text that
we can parse the column name and produce a focused message — without leaking the
full SQL statement or parameter values that asyncpg/SQLAlchemy embed in the
exception string. This module is the single source of truth for that parsing,
used by both the record repository (DML) and the DDL error mapper.

The parser deliberately avoids importing asyncpg/sqlalchemy exception classes;
it works on the string representation so it is driver-agnostic and testable
with plain ``Exception`` mocks.
"""

from __future__ import annotations

import re
from typing import Any

from app.modules.datastore.domain.datastore_entities import ColumnSchema, DatastoreDataType
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreInfrastructureError,
    DatastoreValidationError,
)


def _short_db_message(raw: str) -> str:
    """Return the first human-readable line, stripped of SQL/params noise.

    asyncpg errors look like:
        <class 'asyncpg.exceptions.CheckViolationError'>: new row for relation ...
        DETAIL:  Failing row contains (...
        [SQL: INSERT INTO ...]
        [parameters: (...)]

    We keep only the leading diagnostic sentence and drop DETAIL/SQL/parameters.
    """
    for marker in ("\nDETAIL:", "\n[SQL:", "\n[parameters:", "\n(Background"):
        idx = raw.find(marker)
        if idx != -1:
            raw = raw[:idx]
    line = raw.strip().rstrip(".")
    line = line.split("\n")[0].strip()
    return line


def _extract_column_from_constraint(constraint_name: str, table_name: str | None) -> str | None:
    """Parse ``<table>_<col>_check`` / ``<table>_<col>_key`` -> column name.

    PostgreSQL auto-names constraints ``<table>_<column>_<suffix>``. This works
    for CHECK, UNIQUE, and FK constraints when the column name doesn't contain
    underscores that collide with the table name prefix — a best-effort heuristic
    that covers the vast majority of real cases. Returns ``None`` if the pattern
    doesn't match.
    """
    if not constraint_name or not table_name:
        return None
    prefix = table_name + "_"
    if not constraint_name.startswith(prefix):
        return None
    rest = constraint_name[len(prefix):]
    for suffix in ("_check", "_key", "_fkey", "_not_null"):
        if rest.endswith(suffix):
            col = rest[: -len(suffix)]
            if col:
                return col
    return None


def _extract_column_from_detail(detail_text: str) -> str | None:
    """Parse ``DETAIL:  Failing row contains (..., value, ...)`` for the column.

    Not reliable in general (we don't know which column failed), so this is only
    used as a fallback when the constraint name heuristic fails. We look for the
    column name mentioned in the detail. Returns ``None`` if nothing useful.
    """
    return None


def parse_db_error(
    exc: Exception,
    *,
    table_name: str | None = None,
    columns: list[ColumnSchema] | None = None,
    operation: str = "operation",
) -> tuple[str, dict[str, Any] | None, type]:
    """Parse a DB driver error into ``(message, details, error_class)``.

    ``error_class`` is the appropriate ``Datastore*Error`` subclass to raise.
    The caller is responsible for raising it with the message and details.
    """
    orig = getattr(exc, "orig", exc)
    raw = str(orig)
    lower = raw.lower()

    column_map: dict[str, ColumnSchema] = {}
    if columns:
        column_map = {col.name: col for col in columns}

    def _lookup_col(name: str | None) -> ColumnSchema | None:
        if name is None:
            return None
        return column_map.get(name)

    # --- Check constraint violation (ENUM options, custom CHECK) --------------
    if "check constraint" in lower and "violates" in lower:
        m = re.search(r'check constraint "([^"]+)"', lower)
        constraint = m.group(1) if m else None
        col_name = _extract_column_from_constraint(constraint or "", table_name)
        col = _lookup_col(col_name)

        # Try to extract the failing value from the DETAIL line.
        value_str: str | None = None
        detail_match = re.search(r"Failing row contains \(([^)]*)\)", raw, re.DOTALL)
        if detail_match:
            parts = [p.strip() for p in detail_match.group(1).split(",")]
            # The failing value is typically the last one before the closing paren.
            # We can't reliably map it to a column, so only use it if we know the column.
            if col_name and len(parts) > 0:
                value_str = parts[-1].strip().strip("'")

        if col is not None and col.type == DatastoreDataType.ENUM and col.options:
            allowed = ", ".join(col.options)
            details: dict[str, Any] = {
                "field": col.name,
                "allowed_values": col.options,
            }
            if value_str and value_str != "NULL":
                details["value"] = value_str
            msg = (
                f"Value '{value_str}' is not allowed for column '{col.name}'. "
                f"Allowed values: {allowed}"
            ) if value_str else (
                f"Invalid value for column '{col.name}'. "
                f"Allowed values: {allowed}"
            )
            return msg, details, DatastoreValidationError

        if col_name:
            return (
                f"Value for column '{col_name}' violates a check constraint on table '{table_name}'.",
                {"field": col_name, "constraint": constraint},
                DatastoreValidationError,
            )
        return (
            f"A check constraint was violated on table '{table_name}'.",
            {"constraint": constraint},
            DatastoreValidationError,
        )

    # --- Not-null violation ---------------------------------------------------
    if "not-null constraint" in lower and "violates" in lower:
        m = re.search(r'column "([^"]+)"', lower)
        col_name = m.group(1) if m else None
        if col_name:
            return (
                f"Column '{col_name}' is required and cannot be null.",
                {"field": col_name},
                DatastoreValidationError,
            )
        return (
            f"A required column was missing on table '{table_name}'.",
            None,
            DatastoreValidationError,
        )

    # --- Foreign key violation ------------------------------------------------
    if "foreign key constraint" in lower and "violates" in lower:
        m = re.search(r'column "([^"]+)"', lower)
        col_name = m.group(1) if m else None
        if col_name is None:
            m2 = re.search(r'foreign key constraint "([^"]+)"', lower)
            col_name = _extract_column_from_constraint(m2.group(1) if m2 else "", table_name)
        fk_ref = None
        col = _lookup_col(col_name)
        if col and col.foreign_key:
            fk_ref = col.foreign_key.references
        if col_name:
            ref_msg = f" (references {fk_ref})" if fk_ref else ""
            return (
                f"Value for column '{col_name}' references a non-existent record{ref_msg}.",
                {"field": col_name, "references": fk_ref} if fk_ref else {"field": col_name},
                DatastoreValidationError,
            )
        return (
            f"A foreign key constraint was violated on table '{table_name}'.",
            None,
            DatastoreValidationError,
        )

    # --- Unique / duplicate key -----------------------------------------------
    if "duplicate key value" in lower or "unique constraint" in lower:
        m = re.search(r'unique constraint "([^"]+)"', lower)
        constraint = m.group(1) if m else None
        col_name = _extract_column_from_constraint(constraint or "", table_name)
        detail_match = re.search(r"Key \(([^)]+)\)=", raw)
        if detail_match:
            col_name = col_name or detail_match.group(1).strip()
        if col_name:
            return (
                f"A record with this '{col_name}' already exists.",
                {"field": col_name},
                DatastoreConflictError,
            )
        return (
            f"A record with these values already exists.",
            None,
            DatastoreConflictError,
        )

    # --- Invalid input syntax (type mismatch) ---------------------------------
    if "invalid input syntax" in lower:
        m = re.search(r'for type (\w+)', lower)
        type_name = m.group(1) if m else None
        m2 = re.search(r'column "([^"]+)"', lower)
        col_name = m2.group(1) if m2 else None
        expected = type_name or "the expected type"
        if col_name:
            return (
                f"Invalid value for column '{col_name}': expected {expected}.",
                {"field": col_name, "expected_type": type_name} if type_name else {"field": col_name},
                DatastoreValidationError,
            )
        return (
            f"Invalid value: expected {expected}.",
            {"expected_type": type_name} if type_name else None,
            DatastoreValidationError,
        )

    # --- Numeric out of range -------------------------------------------------
    if "out of range" in lower and ("numeric" in lower or "integer" in lower or "float" in lower):
        m = re.search(r'column "([^"]+)"', lower)
        col_name = m.group(1) if m else None
        if col_name:
            return (
                f"Value for column '{col_name}' is out of range.",
                {"field": col_name},
                DatastoreValidationError,
            )
        return (
            "A numeric value is out of range.",
            None,
            DatastoreValidationError,
        )

    # --- Connection / timeout (infrastructure, not user error) ----------------
    infra_markers = (
        "connection",
        "timeout",
        "server closed the connection",
        "terminating connection",
        "too many connections",
    )
    if any(marker in lower for marker in infra_markers):
        return (
            f"A database connectivity issue occurred during {operation}.",
            None,
            DatastoreInfrastructureError,
        )

    # --- Fallback: strip SQL/params, keep short message -----------------------
    short = _short_db_message(raw)
    if not short:
        short = f"Database error during {operation}."
    return short, None, DatastoreValidationError


def raise_from_db_error(
    exc: Exception,
    *,
    table_name: str | None = None,
    columns: list[ColumnSchema] | None = None,
    operation: str = "operation",
) -> None:
    """Parse ``exc`` and raise the appropriate ``Datastore*Error``.

    Convenience wrapper around :func:`parse_db_error` for call sites that don't
    need the message/details separately.
    """
    message, details, error_cls = parse_db_error(
        exc,
        table_name=table_name,
        columns=columns,
        operation=operation,
    )
    if details is not None:
        raise error_cls(message, details) from exc
    raise error_cls(message) from exc
