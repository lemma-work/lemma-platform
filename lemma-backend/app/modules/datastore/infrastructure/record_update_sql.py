"""Building the record UPDATE statement, and reading its prior image back.

An update returns the row it produced; a trigger that wants to know whether a
value *became* something also needs the row it replaced. Postgres 18 answers
this with `RETURNING OLD.*`, but until then the prior row rides back as one
extra JSON column on the same statement — which costs no round trip and leaves
no window for another writer to slip in between a read and the write.

That choice shapes the whole statement, so the statement is built here rather
than inline: the two shapes and the code that pulls them apart stay together.
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.datastore.domain.datastore_entities import SYSTEM_COLUMNS
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.value_converter import ValueConverter


def build_assignments(
    ctx: TableContext,
    converted_data: dict[str, Any],
    parsed_id: Any,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """Split submitted data into the columns to write, the SET list, and params.

    System columns and the primary key are dropped: an update may not move a
    row's identity or forge its bookkeeping. `updated_at` is set on every
    update and is deliberately not one of the written columns — it changes
    every time and so tells a subscriber nothing.
    """
    mutable_data = {
        key: value
        for key, value in converted_data.items()
        if key not in SYSTEM_COLUMNS and key != ctx.primary_key_column
    }
    if not mutable_data:
        return mutable_data, [], {}

    set_clauses: list[str] = []
    params: dict[str, Any] = {"id": parsed_id}
    column_map = {column.name: column for column in ctx.columns}
    for key, value in mutable_data.items():
        sanitize_identifier(key)
        param_name = f"u_{key}"
        set_clauses.append(f'"{key}" = :{param_name}')
        if key in column_map:
            params[param_name] = ValueConverter.serialize_for_sql(
                value, column_map[key]
            )
        else:
            params[param_name] = value
    set_clauses.append('"updated_at" = CURRENT_TIMESTAMP')
    return mutable_data, set_clauses, params


def previous_image_alias(ctx: TableContext) -> str:
    """Pick a RETURNING alias that no column in this table can shadow.

    Column names are user-chosen and may be any alphanumeric-or-underscore
    string, so a fixed alias could collide with a real column and silently
    overwrite it in the result mapping. Growing the name until it is unique
    against the actual column set is cheap and leaves nothing to chance.
    """
    taken = {column.name for column in ctx.columns}
    alias = "__lemma_previous"
    while alias in taken:
        alias += "_"
    return alias


def build_update_statement(
    ctx: TableContext,
    *,
    set_clauses: list[str],
    where_clauses: list[str],
    capture_previous: bool,
) -> tuple[str, str | None]:
    """Build the update, and the alias its prior row comes back under.

    Two shapes, because capturing a prior image is not free: tables with no
    event subscriber keep the plain single-table update they always had. When
    the image is wanted, the statement self-joins a `FOR UPDATE` sub-select of
    the same row — one statement, one lock, no window in which another writer
    could slip between a read and the write.
    """
    table = f'"{ctx.schema_name}"."{ctx.table_name}"'
    sets = ", ".join(set_clauses)
    where_sql = " AND ".join(where_clauses)
    if not capture_previous:
        return f"UPDATE {table} SET {sets} WHERE {where_sql} RETURNING *", None

    alias = previous_image_alias(ctx)
    primary_key = ctx.primary_key_column
    return (
        f"UPDATE {table} AS t SET {sets} "
        f"FROM (SELECT * FROM {table} WHERE {where_sql} FOR UPDATE) AS prev "
        f'WHERE t."{primary_key}" = prev."{primary_key}" '
        f'RETURNING t.*, to_jsonb(prev)::text AS "{alias}"',
        alias,
    )


def split_previous_image(
    row_mapping: dict[str, Any],
    alias: str | None,
    changed_columns: list[str],
) -> dict[str, Any] | None:
    """Take the prior image out of the returned row, leaving the row itself.

    Mutates ``row_mapping`` so what remains is exactly the table's own columns,
    which is what turns it back into a record entity.
    """
    if alias is None:
        return None
    return extract_previous_image(row_mapping.pop(alias, None), changed_columns)


def extract_previous_image(
    raw: Any, changed_columns: list[str]
) -> dict[str, Any] | None:
    """Narrow the returned pre-image row to the columns the write touched.

    Carrying the whole prior row would roughly double every update event to
    answer a question nobody asks about untouched columns. The values stay in
    their JSON form, which is the form the event is published in and the form a
    match condition compares against.
    """
    if raw is None:
        return None
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    return {column: raw.get(column) for column in changed_columns}
