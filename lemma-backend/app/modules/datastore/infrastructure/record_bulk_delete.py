"""Delete many records in one transaction, and say what did not match.

`delete_record` is right for one row and wrong N times, for the same reasons
`record_bulk_update` gives: it opens its own session, sets the RLS context,
runs the DELETE, appends its change and commits, per row.

Atomicity is why this one had to move, though, not latency. `PS-DATA-013`
promises a bulk change applies all of its records or none of them, and that a
rejected one is named. Bulk delete did neither: it committed each row on its
own, so a batch that failed partway left the earlier rows destroyed with the
caller holding an error and no way to learn which; and an id matching nothing
was swallowed and quietly left out of the count, so "deleted 3" and "deleted 3
of the 5 you asked for" read identically. Destroyed rows are the worst place
in the module to leave that ambiguity.

Repeated ids are collapsed rather than reported: asking twice for the same row
to be gone is not a request that failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datastore.changes.capture import ChangeCapture
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreRecordNotFoundError,
)
from app.modules.datastore.infrastructure.record_bulk_sql import (
    chunk_rows,
    unnest_delete_statement,
)
from app.modules.datastore.infrastructure.record_errors import (
    raise_record_write_error,
)
from app.modules.datastore.services.table_context import TableContext

if TYPE_CHECKING:
    from app.modules.datastore.infrastructure.record_repository import (
        DatastoreRecordRepository,
    )

logger = get_logger(__name__)

#: A primary-key value as it arrives from the API: the column may be UUID,
#: SERIAL/INTEGER or TEXT, so the id the caller names is any of those.
RecordId = str | int | UUID

#: Bind parameters for one statement. Values are whatever the column converts
#: to, which is the driver's business and not this module's.
SqlParams = dict[str, object]

#: How many unmatched ids the refusal lists before summarising the rest. Enough
#: to fix a mistake from the message alone, short of pasting the batch back.
_NAMED_MISSING_IDS = 5


def prepare_bulk_deletes(
    ctx: TableContext,
    record_ids: list[RecordId],
    user_id: UUID,
    *,
    enforce_user_scope: bool,
) -> list[tuple[RecordId, str, SqlParams]]:
    """One DELETE per row, ready to run inside a single transaction.

    Pure statement construction, mirroring ``prepare_bulk_updates``. Each tuple
    is ``(record_id, sql, params)`` — the caller keeps the id so it can name
    the ones that matched nothing.
    """
    prepared: list[tuple[RecordId, str, SqlParams]] = []
    seen: set[str] = set()
    for record_id in record_ids:
        if str(record_id) in seen:
            continue
        seen.add(str(record_id))
        params: SqlParams = {"id": ctx.parse_primary_key(record_id)}
        where_clauses = [f'"{ctx.primary_key_column}" = :id']
        if ctx.enable_rls and enforce_user_scope:
            where_clauses.append('"user_id" = :current_user_id')
            params["current_user_id"] = str(user_id)
        prepared.append(
            (
                record_id,
                (
                    f'DELETE FROM "{ctx.schema_name}"."{ctx.table_name}" '
                    f"WHERE {' AND '.join(where_clauses)} RETURNING *"
                ),
                params,
            )
        )
    return prepared


def batch_bulk_deletes(
    ctx: TableContext,
    prepared: list[tuple[RecordId, str, SqlParams]],
    user_id: UUID,
    *,
    enforce_user_scope: bool,
) -> list[tuple[list[tuple[RecordId, str, SqlParams]], str, SqlParams]]:
    """Fold the per-row deletes into fixed-shape ``pk = ANY(:ids)`` statements.

    Each tuple is ``(rows, sql, params)``: the SQL text is the same for every
    batch size, and the rows are kept so the ids that matched nothing can still
    be named. Falls back to one statement per row only if the primary key's
    array type cannot be named (which no supported key type hits today).
    """
    scoped = ctx.enable_rls and enforce_user_scope
    sql = unnest_delete_statement(ctx, scoped_to_user=scoped)
    if sql is None:
        return [([row], row[1], row[2]) for row in prepared]
    batches = []
    for chunk in chunk_rows(prepared, sized=False):
        params: SqlParams = {"ids": [row_params["id"] for _, _, row_params in chunk]}
        if scoped:
            params["current_user_id"] = str(user_id)
        batches.append((chunk, sql, params))
    return batches


def _missing_ids_message(missing: list[RecordId], requested: int) -> str:
    named = ", ".join(f"'{record_id}'" for record_id in missing[:_NAMED_MISSING_IDS])
    if len(missing) > _NAMED_MISSING_IDS:
        named += f" and {len(missing) - _NAMED_MISSING_IDS} more"
    return (
        f"Nothing was deleted: {len(missing)} of the {requested} record ids "
        f"matched no record you can delete ({named}). A bulk delete applies "
        "every id or none of them."
    )


async def bulk_delete_records(
    repository: "DatastoreRecordRepository",
    ctx: TableContext,
    record_ids: list[RecordId],
    user_id: UUID,
    *,
    changes: ChangeCapture,
    enforce_user_scope: bool = True,
) -> int:
    """Delete every named row in one transaction, or none of them.

    A free function rather than a repository method, like its update twin:
    this is a cohesive unit that does not need the class.
    """
    if not record_ids:
        return 0
    prepared = prepare_bulk_deletes(
        ctx, record_ids, user_id, enforce_user_scope=enforce_user_scope
    )

    schema_manager = repository.schema_manager
    await changes.prepare()
    try:
        async with schema_manager.session_factory() as session:
            principal = ctx.row_principal(user_id, is_pod_admin=not enforce_user_scope)
            if principal is not None:
                await schema_manager.set_rls_context(session, principal)

            await changes.begin(session)
            _deleted, missing = await _delete_batches(
                session,
                batch_bulk_deletes(
                    ctx, prepared, user_id, enforce_user_scope=enforce_user_scope
                ),
                ctx,
                changes,
            )

            if missing:
                # Rolls the whole batch back, which is the point: a partial
                # destruction the caller cannot enumerate is worse than none.
                await session.rollback()
                raise DatastoreRecordNotFoundError(
                    _missing_ids_message(missing, len(prepared))
                )

            await changes.wake_handoff(session)
            await session.commit()
            return len(prepared)
    except DBAPIError as exc:
        logger.debug("datastore.record.bulk_delete.propagated", exc_info=True)
        raise_record_write_error(exc, operation="bulk delete records", ctx=ctx)


async def _delete_batches(
    session: AsyncSession,
    batches: list[tuple[list[tuple[RecordId, str, SqlParams]], str, SqlParams]],
    ctx: TableContext,
    changes: ChangeCapture,
) -> tuple[list[dict[str, object]], list[RecordId]]:
    """Run every batch; the rows deleted, and the ids that matched nothing."""
    deleted_rows: list[dict[str, object]] = []
    missing: list[RecordId] = []
    try:
        for rows, sql, params in batches:
            sql, params = changes.wrap(sql, params, op="D")
            result = await session.execute(text(sql), params)
            deleted = [
                changes.row(row) if changes is not None else dict(row._mapping)
                for row in result.fetchall()
            ]
            matched = {str(row.get(ctx.primary_key_column)) for row in deleted}
            # Collected rather than raised on the spot: the whole batch is
            # rolled back either way, and one refusal naming every bad id is
            # worth more than N attempts.
            missing.extend(
                record_id
                for record_id, _, row_params in rows
                if str(row_params["id"]) not in matched
            )
            deleted_rows.extend(deleted)
    except IntegrityError as exc:
        await session.rollback()
        raise DatastoreConflictError(
            "Nothing was deleted: one of these records is still "
            "referenced by other records. Remove or reassign those "
            "first."
        ) from exc
    return deleted_rows, missing
