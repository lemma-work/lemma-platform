"""Decide a page's rows and its total without always counting the table.

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
