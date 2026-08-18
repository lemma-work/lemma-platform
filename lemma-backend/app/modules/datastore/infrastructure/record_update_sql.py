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
from uuid import UUID

from app.modules.datastore.domain.datastore_entities import SYSTEM_COLUMNS
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.record_validator import convert_record
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


# Postgres carries at most 65535 bind parameters in one extended-protocol
# message. A bulk write of wide rows can exceed that, so the multi-row form is
# split into chunks that cannot; the margin leaves room for anything the
# statement binds besides the row values themselves.
_MAX_BIND_PARAMETERS = 60_000


def chunk_for_parameter_limit(
    records: list[dict[str, Any]], columns_per_row: int
) -> list[list[dict[str, Any]]]:
    """Split rows into batches that fit inside one statement's parameter budget."""
    if not records:
        return []
    per_chunk = max(1, _MAX_BIND_PARAMETERS // max(1, columns_per_row))
    return [
        records[start : start + per_chunk]
        for start in range(0, len(records), per_chunk)
    ]


def bulk_insert_statement(ctx: TableContext, ordered_keys: list[str]) -> str:
    """The single-row INSERT template driven by executemany."""
    columns_sql = ", ".join(f'"{key}"' for key in ordered_keys)
    placeholders_sql = ", ".join(f":{key}" for key in ordered_keys)
    return (
        f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" '
        f"({columns_sql}) VALUES ({placeholders_sql})"
    )


def bulk_conflict_clause(ctx: TableContext, ordered_keys: list[str]) -> str:
    update_columns = [
        key for key in ordered_keys if key not in {ctx.primary_key_column, "created_at"}
    ]
    set_clauses = [f'"{key}" = EXCLUDED."{key}"' for key in update_columns]
    set_clauses.append('"updated_at" = CURRENT_TIMESTAMP')
    return (
        f' ON CONFLICT ("{ctx.primary_key_column}") DO UPDATE SET '
        f"{', '.join(set_clauses)}"
    )


def bulk_returning_statement(
    ctx: TableContext,
    ordered_keys: list[str],
    chunk: list[dict[str, Any]],
    conflict_sql: str,
) -> tuple[str, dict[str, Any]]:
    """A multi-row INSERT that hands every written row back.

    executemany cannot return rows, so a bulk write that has an event subscriber
    is expressed as one statement with N value tuples instead. Without this the
    events would have to be built from what the caller submitted, and a
    condition on a column the database defaulted would silently never match.
    """
    columns_sql = ", ".join(f'"{key}"' for key in ordered_keys)
    params: dict[str, Any] = {}
    tuples: list[str] = []
    for index, record in enumerate(chunk):
        placeholders: list[str] = []
        for key in ordered_keys:
            # `r{index}_` cannot collide: the same name requires the same index
            # and the same column, and column names are already sanitized.
            name = f"r{index}_{key}"
            params[name] = record.get(key)
            placeholders.append(f":{name}")
        tuples.append(f"({', '.join(placeholders)})")

    return (
        f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" ({columns_sql}) '
        f"VALUES {', '.join(tuples)}{conflict_sql} RETURNING *",
        params,
    )


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


def order_bulk_keys(primary_key: str, all_keys: set[str]) -> list[str]:
    """Column order for a bulk write: primary key first, then alphabetical.

    Stable ordering is what lets the generated SQL be reused across calls with
    the same column set.
    """
    ordered = [primary_key] if primary_key in all_keys else []
    ordered.extend(sorted(key for key in all_keys if key != primary_key))
    return ordered


def build_bulk_statements(
    ctx,
    ordered_keys: list[str],
    prepared_records: list[dict],
    conflict_sql: str,
) -> list[tuple[str, dict]]:
    """One (sql, params) pair per chunk, for the RETURNING form of a bulk write.

    Built by the caller *before* it opens its transaction: none of this needs a
    connection, and doing it between the executes inside one meant a bulk write
    held a connection -- with the write's row locks -- while it assembled
    strings. Measured at eleven holds on a real-LLM e2e run, worst 784ms.
    """
    return [
        bulk_returning_statement(ctx, ordered_keys, chunk, conflict_sql)
        for chunk in chunk_for_parameter_limit(prepared_records, len(ordered_keys))
    ]


def prepare_bulk_updates(
    ctx: TableContext,
    updates: list[tuple[Any, dict[str, Any]]],
    user_id: UUID,
    *,
    enforce_user_scope: bool,
    capture_previous: bool,
) -> list[tuple[str, dict[str, Any], list[str], str | None]]:
    """Build one UPDATE per row, ready to run inside a single transaction.

    Pure, and here rather than on the repository, because it is statement
    construction and this module already owns that for the single-row path.
    Each tuple is ``(sql, params, changed_columns, previous_alias)``.

    Rows whose payload changes nothing are dropped rather than issued: the
    single-row path returns the untouched record in that case, but a bulk
    caller only counts, so the statement would be a round trip for no effect.
    """
    prepared: list[tuple[str, dict[str, Any], list[str], str | None]] = []
    for record_id, data in updates:
        parsed_id = ctx.parse_primary_key(record_id)
        mutable_data, set_clauses, params = build_assignments(
            ctx, convert_record(ctx.columns, data), parsed_id
        )
        if not mutable_data:
            continue
        where_clauses = [f'"{ctx.primary_key_column}" = :id']
        if ctx.enable_rls and enforce_user_scope:
            where_clauses.append('"user_id" = :current_user_id')
            params["current_user_id"] = str(user_id)
        sql, previous_alias = build_update_statement(
            ctx,
            set_clauses=set_clauses,
            where_clauses=where_clauses,
            capture_previous=capture_previous,
        )
        prepared.append((sql, params, sorted(mutable_data.keys()), previous_alias))
    return prepared
