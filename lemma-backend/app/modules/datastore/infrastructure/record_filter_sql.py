"""Turn one parsed record filter into a WHERE clause and its bound parameter.

Lifted out of ``record_repository`` so the operator dispatch lives on its own,
where it can be read and extended without deepening the query builder it feeds.
"""

from __future__ import annotations

from typing import Any

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.services.value_converter import ValueConverter

_SCALAR_OPERATORS: dict[str, str] = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
    "ilike": "ILIKE",
}

_ALLOWED_OPERATORS = [*_SCALAR_OPERATORS, "in"]


def _convert_filter_value(value: Any, col: Any) -> Any:
    """Coerce a filter value to its column type, best-effort.

    An unconvertible value passes through unchanged (the driver then rejects a
    genuine type mismatch). ``col`` is ``None`` when the field is not a known
    column.
    """
    if col is None:
        return value
    try:
        return ValueConverter.convert_value(value, col)
    except ValueError:
        return value


def build_filter_predicate(
    field: str,
    op: str,
    value: Any,
    col: Any,
    param_name: str,
) -> tuple[str, dict[str, Any]]:
    """``(where_clause, params_to_bind)`` for one filter.

    ``field`` must already be identifier-sanitized by the caller. ``in`` binds a
    Python list as a Postgres array and tests membership with ``= ANY(:p)`` — the
    parameterized form of ``IN (...)``; an empty list matches nothing and binds
    no (ambiguously-typed) array parameter.
    """
    scalar = _SCALAR_OPERATORS.get(op)
    if scalar is not None:
        return f'"{field}" {scalar} :{param_name}', {
            param_name: _convert_filter_value(value, col)
        }

    if op == "in":
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        if not values:
            return "1 = 0", {}
        return f'"{field}" = ANY(:{param_name})', {
            param_name: [_convert_filter_value(item, col) for item in values]
        }

    raise DatastoreValidationError(
        f"Unsupported filter operator '{op}'",
        details={"operator": op, "allowed_operators": _ALLOWED_OPERATORS},
    )
