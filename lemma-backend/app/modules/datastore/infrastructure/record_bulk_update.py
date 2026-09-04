"""Run many prepared record updates inside one transaction.

`update_record` is right for one row and wrong N times: it opens its own
session, sets the RLS context, runs the UPDATE, stages its event and commits.
A bulk update therefore paid four round trips *and a fresh connection checkout*
per row, while `_bulk_write_records` had been doing the batched thing for
create and upsert all along. Production measured the difference on
`records/bulk/update` at p50 2.3s for a modest batch.

The rows still need one statement each, because each carries its own SET
clause. What they no longer need is a session, an RLS round trip, an event
staging and a commit each.

Atomicity comes with it, and matters more than the latency: per-row commits
meant a batch failing halfway left the first half written, with the caller
holding an error and no way to tell which rows had landed.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.domain.events import DomainEvent
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreRecordNotFoundError
from app.modules.datastore.infrastructure.transactional_events import (
    ensure_datastore_event_outbox,
    stage_domain_events,
)
from app.modules.datastore.infrastructure.record_update_sql import (
    prepare_bulk_updates,
    split_previous_image,
)
from app.modules.datastore.infrastructure.record_errors import (
    raise_record_write_error,
)
from app.modules.datastore.services.table_context import TableContext

logger = get_logger(__name__)

PreparedUpdate = tuple[str, dict[str, Any], list[str], str | None]


async def bulk_update_records(
    repository: Any,
    ctx: TableContext,
    updates: list[tuple[Any, dict[str, Any]]],
    user_id: UUID,
    *,
    enforce_user_scope: bool = True,
    event_factory: Callable[..., DomainEvent] | None = None,
) -> int:
    """Apply many updates in one transaction, like create and upsert do.

    A free function rather than a repository method: `record_repository` is
    already past the size the architecture gate allows, and this is a cohesive
    unit that does not need the class.
    """
    if not updates:
        return 0
    if event_factory is not None:
        await ensure_datastore_event_outbox()
    prepared = prepare_bulk_updates(
        ctx,
        updates,
        user_id,
        enforce_user_scope=enforce_user_scope,
        capture_previous=event_factory is not None,
    )
    if not prepared:
        return 0

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
            updated = 0
            for sql, params, changed_columns, previous_alias in prepared:
                result = await session.execute(text(sql), params)
                row = result.fetchone()
                if not row:
                    # Rolls the whole batch back, which is the point: a partial
                    # write the caller cannot enumerate is worse than none.
                    raise DatastoreRecordNotFoundError(
                        "Record not found or update failed"
                    )
                updated += 1
                if event_factory is None:
                    continue
                row_mapping = dict(row._mapping)
                events.append(
                    event_factory(
                        row_to_entity(row_mapping, ctx),
                        changed_columns,
                        split_previous_image(
                            row_mapping, previous_alias, changed_columns
                        ),
                    )
                )

            if events:
                await stage_domain_events(session, events)
            await session.commit()
            return updated
    except DBAPIError as exc:
        logger.debug("datastore.record.bulk_update.propagated", exc_info=True)
        raise_record_write_error(exc, operation="bulk update records", ctx=ctx)
