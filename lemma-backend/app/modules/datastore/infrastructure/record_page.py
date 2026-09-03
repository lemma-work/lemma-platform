"""Decide a page's rows, its order, and its total without always counting.

`SELECT COUNT(*)` used to run on every page request, over the whole of the
user's table, unindexed, purely to decide whether to emit a next-page token.

Asking for one row more than the caller wanted answers that question directly.
When the extra row does not arrive, the page is the last one and the total is
arithmetic -- offset plus what we hold -- so the count can be skipped entirely.
When it does arrive there is more data, and the count still runs, because
`total` is a public response field the frontend renders and it may not degrade
into an estimate.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text

from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier
from app.modules.datastore.services.table_context import TableContext


def order_by_clause(ctx: TableContext, sorts: Sequence[tuple[str, str]] | None) -> str:
    """The ORDER BY a listing pages over, always ending in a unique column.

    Offset paging is only well defined over a total order. Sorting by a column
    whose values repeat -- a status, a date, an enum -- leaves the order among
    equal rows to the planner, and PostgreSQL is then free to place a row on
    two consecutive pages or on neither. `PS-DATA-011` promises paging an
    unchanging table returns every record exactly once, so the primary key is
    appended as the tiebreak whenever the caller's own sort does not already
    include it. The default sort has always done this; an explicit one did not.
    """
    if not sorts:
        if any(column.name == "created_at" for column in ctx.columns):
            return f'"created_at" DESC, "{ctx.primary_key_column}" DESC'
        return f'"{ctx.primary_key_column}" DESC'

    clauses: list[str] = []
    order_dir = "ASC"
    for field, direction in sorts:
        sanitize_identifier(field)
        order_dir = "DESC" if direction.lower() == "desc" else "ASC"
        clauses.append(f'"{field}" {order_dir}')
    if all(field != ctx.primary_key_column for field, _ in sorts):
        # Matching the last clause's direction keeps the tiebreak reading as
        # part of the caller's sort rather than as a reversal tacked onto it.
        clauses.append(f'"{ctx.primary_key_column}" {order_dir}')
    return ", ".join(clauses)


async def rows_and_total(
    session: Any,
    *,
    list_sql: str,
    count_sql: str,
    params: dict[str, Any],
    limit: int,
    offset: int,
) -> tuple[Sequence[Any], int]:
    """Return at most ``limit`` rows plus the table's exact total."""
    rows = (await session.execute(text(list_sql), params)).fetchall()
    if len(rows) <= limit:
        return rows, offset + len(rows)
    counted = await session.execute(
        text(count_sql),
        {key: value for key, value in params.items() if key not in ("limit", "offset")},
    )
    return rows[:limit], counted.scalar() or 0
