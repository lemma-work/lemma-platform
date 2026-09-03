"""The optimistic-concurrency guard on a record update.

`record.update` carried no version, ETag or `If-Match`, and the statement it
emitted had no predicate on prior state -- so two clients that read a row and
each patched the same field both succeeded, and the later one silently won. The
record API is what apps and agents bind to, and a row edited from a surface and
a UI at once lost an edit with no error anywhere.

The guard is opt-in and costs nothing when it is absent: a caller that omits
``expected_updated_at`` gets the last-writer-wins update it always did.

Kept out of the repository because it is a policy with three parts -- what the
table must have, what the statement gains, and how to tell the two failures
apart -- rather than one more shape of SQL for the query builder to assemble.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreRecordNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.services.table_context import TableContext

#: The column the guard compares. Materialized on every table created since
#: system columns existed; an older one has to be told, not silently exempted.
UPDATED_AT_COLUMN = "updated_at"


def require_updated_at_column(ctx: TableContext) -> None:
    """Refuse the guard on a table that cannot carry it.

    Every table the record API writes has the column materialized, so this is a
    backstop -- but the failure it replaces is a driver error naming a column
    the caller never mentioned, which says nothing about what to do next.
    """
    if not any(column.name == UPDATED_AT_COLUMN for column in ctx.columns):
        raise DatastoreValidationError(
            f"Table '{ctx.table_name}' has no '{UPDATED_AT_COLUMN}' column, so "
            "'expected_updated_at' cannot be checked. Omit it, or add the column."
        )


def apply_expected_updated_at(
    ctx: TableContext,
    where_clauses: list[str],
    params: dict[str, object],
    expected_updated_at: datetime,
) -> None:
    """Claim the row only while it still looks the way the caller read it.

    In the same statement that writes it, so there is no window between the
    check and the write for a third party to slip through.
    """
    require_updated_at_column(ctx)
    where_clauses.append(f'"{UPDATED_AT_COLUMN}" = :expected_updated_at')
    params["expected_updated_at"] = expected_updated_at


async def raise_missing_or_stale(
    session: AsyncSession,
    ctx: TableContext,
    *,
    where_clauses: list[str],
    params: dict[str, object],
) -> None:
    """Say which of the two reasons a guarded write matched nothing.

    A deleted row and a row somebody else edited are indistinguishable from the
    update's own result, and the caller has to do different things about them:
    one is gone, the other moved on. ``where_clauses`` is the row scope *without*
    the guard, so this asks only whether the row is still there. On the failure
    path only -- an unguarded update that matched nothing pays nothing.
    """
    exists = await session.execute(
        text(
            f'SELECT 1 FROM "{ctx.schema_name}"."{ctx.table_name}" '
            f"WHERE {' AND '.join(where_clauses)}"
        ),
        params,
    )
    if exists.fetchone() is None:
        raise DatastoreRecordNotFoundError("Record not found or update failed")
    raise DatastoreConflictError(
        "The record changed since it was read, so this update was not applied. "
        "Read it again and re-apply the change."
    )


def primary_key_scope(
    ctx: TableContext,
    record_id: int | str | UUID,
    user_id: UUID,
    *,
    enforce_user_scope: bool,
) -> tuple[list[str], dict[str, object]]:
    """The row scope a guarded update falls back to when it matched nothing.

    Rebuilt rather than reused: by the time the probe runs, the update's own
    clause list carries the guard, and the probe must ask *without* it. Mirrors
    ``DatastoreRecordRepository._apply_current_user_scope`` -- the two say the
    same thing about who may see a row, and must keep saying it.
    """
    where_clauses = [f'"{ctx.primary_key_column}" = :id']
    params: dict[str, object] = {"id": record_id}
    if ctx.enable_rls and enforce_user_scope:
        where_clauses.append('"user_id" = :current_user_id')
        params["current_user_id"] = str(user_id)
    return where_clauses, params
