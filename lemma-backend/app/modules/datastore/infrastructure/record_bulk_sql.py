"""Fixed-shape bulk statements: one array bind per column, expanded by `unnest`.

A multi-row ``VALUES`` list binds every cell under its own name, so its SQL text
is different for every row count. Each distinct text was a new entry in
SQLAlchemy's compiled cache (~15 MB for a 1000x10 write), and even with that
cache off it is up to a megabyte of SQL and 60k binds to parse, trace and log.

Here the text depends only on the table, the column set and the clause options:
each column travels as one typed array and ``unnest`` zips them back into rows.
Ten rows and ten thousand rows produce byte-identical SQL.

The element type of each array must be named in the statement, so it is taken
from the column's datastore type. A key whose type cannot be named with
certainty -- a computed column, a VECTOR column (its values may arrive as a
Python list, which a ``text[]`` element cannot hold), ``created_at`` /
``updated_at`` passed through unconverted, or a key the schema does not know --
returns ``None`` from these builders, and the caller keeps the per-row form for
that call. That preserves exactly the behaviour (and the database error) the
caller had before, rather than guessing a cast that might change it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar

from app.modules.datastore.domain.datastore_entities import DatastoreDataType
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier
from app.modules.datastore.services.table_context import TableContext

Row = TypeVar("Row")

_ARRAY_ELEMENT_TYPES: dict[DatastoreDataType, str] = {
    DatastoreDataType.TEXT: "text",
    DatastoreDataType.FILE_PATH: "text",
    DatastoreDataType.ENUM: "text",
    DatastoreDataType.INTEGER: "integer",
    DatastoreDataType.SERIAL: "integer",
    DatastoreDataType.FLOAT: "numeric",
    DatastoreDataType.BOOLEAN: "boolean",
    DatastoreDataType.DATE: "date",
    DatastoreDataType.DATETIME: "timestamptz",
    DatastoreDataType.JSON: "jsonb",
    DatastoreDataType.UUID: "uuid",
    DatastoreDataType.USER: "uuid",
}

#: Rows per statement. Binds no longer grow with rows, so this bounds the
#: array payload (and the rows held locked while events are built) instead.
MAX_ROWS_PER_STATEMENT = 5_000
#: Rough byte budget per statement for string-ish values.
MAX_BYTES_PER_STATEMENT = 32 * 1024 * 1024


def array_element_type(ctx: TableContext, key: str) -> str | None:
    """The Postgres element type for ``key``'s array, or None if not certain."""
    column = ctx.get_column(key)
    if column is None:
        if key == "user_id":
            return "uuid"
        if key == ctx.primary_key_column:
            column = ctx.get_primary_key_schema()
        else:
            return None
    if column.computed:
        return None
    return _ARRAY_ELEMENT_TYPES.get(column.type)


def _array_types(ctx: TableContext, keys: list[str]) -> list[str] | None:
    types: list[str] = []
    for key in keys:
        element = array_element_type(ctx, key)
        if element is None:
            return None
        types.append(element)
    return types


def _unnest_sql(types: list[str]) -> str:
    return ", ".join(
        f"CAST(:c{index} AS {element}[])" for index, element in enumerate(types)
    )


def column_arrays(
    keys: list[str], rows: Sequence[Mapping[str, object]]
) -> dict[str, list[object]]:
    """One list per column; a key absent from a row is NULL, as before."""
    return {
        f"c{index}": [row.get(key) for row in rows] for index, key in enumerate(keys)
    }


def _approximate_size(row: object) -> int:
    if not isinstance(row, Mapping):
        return 0
    size = 0
    for value in row.values():
        if isinstance(value, (str, bytes)):
            size += len(value)
        else:
            size += 16
    return size


def chunk_rows(rows: Sequence[Row], *, sized: bool = True) -> list[list[Row]]:
    """Split rows under the row budget and, for dict rows, the byte budget."""
    chunks: list[list[Row]] = []
    current: list[Row] = []
    current_bytes = 0
    for row in rows:
        row_bytes = _approximate_size(row) if sized else 0
        if current and (
            len(current) >= MAX_ROWS_PER_STATEMENT
            or current_bytes + row_bytes > MAX_BYTES_PER_STATEMENT
        ):
            chunks.append(current)
            current, current_bytes = [], 0
        current.append(row)
        current_bytes += row_bytes
    if current:
        chunks.append(current)
    return chunks


def unnest_insert_statement(
    ctx: TableContext, ordered_keys: list[str], conflict_sql: str
) -> str | None:
    """``INSERT ... SELECT * FROM unnest(...) [ON CONFLICT ...] RETURNING *``."""
    types = _array_types(ctx, ordered_keys)
    if types is None or not ordered_keys:
        return None
    columns_sql = ", ".join(f'"{key}"' for key in ordered_keys)
    return (
        f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" ({columns_sql}) '
        f"SELECT * FROM unnest({_unnest_sql(types)}){conflict_sql} RETURNING *"
    )


def unnest_update_statement(
    ctx: TableContext,
    columns: list[str],
    *,
    scoped_to_user: bool,
    previous_alias: str | None,
) -> str | None:
    """One UPDATE for every row sharing a column set.

    Bind ``c0`` is the primary keys, ``c1..`` the columns in ``columns`` order.
    When the prior image is wanted, the same ``FOR UPDATE`` sub-select the
    single-row form uses is joined in, keyed by the same id array.
    """
    primary_key = ctx.primary_key_column
    types = _array_types(ctx, [primary_key, *columns])
    if types is None:
        return None
    for column in columns:
        sanitize_identifier(column)
    table = f'"{ctx.schema_name}"."{ctx.table_name}"'
    aliases = ", ".join(f"c{index}" for index in range(len(types)))
    sets = [f'"{column}" = v.c{index + 1}' for index, column in enumerate(columns)]
    sets.append('"updated_at" = CURRENT_TIMESTAMP')
    scope = ' AND t."user_id" = :current_user_id' if scoped_to_user else ""
    source = f"unnest({_unnest_sql(types)}) AS v({aliases})"
    if previous_alias is None:
        return (
            f"UPDATE {table} AS t SET {', '.join(sets)} FROM {source} "
            f'WHERE t."{primary_key}" = v.c0{scope} RETURNING t.*'
        )
    prev_scope = ' AND "user_id" = :current_user_id' if scoped_to_user else ""
    return (
        f"UPDATE {table} AS t SET {', '.join(sets)} FROM {source}, "
        f'(SELECT * FROM {table} WHERE "{primary_key}" = ANY(CAST(:c0 AS '
        f"{types[0]}[])){prev_scope} FOR UPDATE) AS prev "
        f'WHERE t."{primary_key}" = v.c0 AND prev."{primary_key}" = v.c0{scope} '
        f'RETURNING t.*, to_jsonb(prev)::text AS "{previous_alias}"'
    )


def unnest_delete_statement(ctx: TableContext, *, scoped_to_user: bool) -> str | None:
    """``DELETE ... WHERE pk = ANY(CAST(:ids AS type[])) RETURNING *``."""
    element = array_element_type(ctx, ctx.primary_key_column)
    if element is None:
        return None
    scope = ' AND "user_id" = :current_user_id' if scoped_to_user else ""
    return (
        f'DELETE FROM "{ctx.schema_name}"."{ctx.table_name}" '
        f'WHERE "{ctx.primary_key_column}" = ANY(CAST(:ids AS {element}[]))'
        f"{scope} RETURNING *"
    )
