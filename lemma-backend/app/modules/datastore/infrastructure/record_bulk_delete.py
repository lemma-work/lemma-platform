"""Delete many records in one transaction, and say what did not match.

`delete_record` is right for one row and wrong N times, for the same reasons
`record_bulk_update` gives: it opens its own session, sets the RLS context,
runs the DELETE, stages its event and commits, per row.

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

from typing import TYPE_CHECKING, Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.domain.events import DomainEvent
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreRecordNotFoundError,
)
from app.modules.datastore.infrastructure.record_errors import (
    raise_record_write_error,
)
from app.modules.datastore.infrastructure.transactional_events import (
    ensure_datastore_event_outbox,
    stage_domain_events,
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
                f'DELETE FROM "{ctx.schema_name}"."{ctx.table_name}" '
                f"WHERE {' AND '.join(where_clauses)} RETURNING *",
                params,
            )
        )
    return prepared


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
    enforce_user_scope: bool = True,
    event_factory: Callable[..., DomainEvent] | None = None,
) -> int:
    """Delete every named row in one transaction, or none of them.

    A free function rather than a repository method, like its update twin:
    `record_repository` is already past the size the architecture gate allows,
    and this is a cohesive unit that does not need the class.
    """
    if not record_ids:
        return 0
    if event_factory is not None:
        await ensure_datastore_event_outbox()
    prepared = prepare_bulk_deletes(
        ctx, record_ids, user_id, enforce_user_scope=enforce_user_scope
    )

    schema_manager = repository.schema_manager
    row_to_entity = repository._row_to_entity
    try:
        async with schema_manager.session_factory() as session:
            if ctx.enable_rls:
                await schema_manager.set_rls_context(
                    session,
                    user_id,
                    is_pod_admin=not enforce_user_scope,
                )

            events: list[DomainEvent] = []
            missing: list[RecordId] = []
            try:
                for record_id, sql, params in prepared:
                    result = await session.execute(text(sql), params)
                    row = result.fetchone()
                    if row is None:
                        # Collected rather than raised on the spot: the whole
                        # batch is rolled back either way, and one refusal
                        # naming every bad id is worth more than N attempts.
                        missing.append(record_id)
                        continue
                    if event_factory is None:
                        continue
                    events.append(event_factory(row_to_entity(dict(row._mapping), ctx)))
            except IntegrityError as exc:
                await session.rollback()
                raise DatastoreConflictError(
                    "Nothing was deleted: one of these records is still "
                    "referenced by other records. Remove or reassign those "
                    "first."
                ) from exc

            if missing:
                # Rolls the whole batch back, which is the point: a partial
                # destruction the caller cannot enumerate is worse than none.
                await session.rollback()
                raise DatastoreRecordNotFoundError(
                    _missing_ids_message(missing, len(prepared))
                )

            if events:
                await stage_domain_events(session, events)
            await session.commit()
            return len(prepared)
    except DBAPIError as exc:
        logger.debug("datastore.record.bulk_delete.propagated", exc_info=True)
        raise_record_write_error(exc, operation="bulk delete records", ctx=ctx)
