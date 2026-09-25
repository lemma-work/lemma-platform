"""Run many prepared record updates inside one transaction.

`update_record` is right for one row and wrong N times: it opens its own
session, sets the RLS context, runs the UPDATE, stages its event and commits.
A bulk update therefore paid four round trips *and a fresh connection checkout*
per row, while `_bulk_write_records` had been doing the batched thing for
create and upsert all along. Production measured the difference on
`records/bulk/update` at p50 2.3s for a modest batch.

Rows are grouped by the columns they change, and each group is one
fixed-shape ``UPDATE ... FROM unnest(...)`` whose text does not depend on the
row count. Only a batch that names an id twice, or touches a column whose
array type cannot be named, falls back to one statement per row.

Atomicity comes with it, and matters more than the latency: per-row commits
meant a batch failing halfway left the first half written, with the caller
holding an error and no way to tell which rows had landed.
"""

from __future__ import annotations

from collections.abc import Sequence
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
from app.modules.datastore.infrastructure.record_bulk_sql import (
    chunk_rows,
    unnest_update_statement,
)
from app.modules.datastore.infrastructure.record_update_sql import (
    build_assignments,
    previous_image_alias,
    prepare_bulk_updates,
    split_previous_image,
)
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.infrastructure.record_errors import (
    raise_record_write_error,
)
from app.modules.datastore.services.table_context import TableContext

logger = get_logger(__name__)

RecordKey = int | str | UUID
PreparedUpdate = tuple[str, dict[str, Any], list[str], str | None]
#: ``(sql, params, changed_columns, previous_alias, expected_rows)``.
GroupedUpdate = tuple[str, dict[str, Any], list[str], str | None, int]


def prepare_grouped_updates(
    ctx: TableContext,
    updates: Sequence[tuple[object, dict[str, object]]],
    user_id: UUID,
    *,
    enforce_user_scope: bool,
    capture_previous: bool,
) -> list[GroupedUpdate] | None:
    """One fixed-shape ``UPDATE ... FROM unnest(...)`` per changed-column set.

    The SQL text depends on the column set, never on how many rows share it.
    Returns None -- and the caller keeps one statement per row -- when an id
    repeats (a join applies one arbitrary source row per target, where the
    per-row form applied them in order) or a column's array type cannot be
    named with certainty (see ``record_bulk_sql``).
    """
    scoped = ctx.enable_rls and enforce_user_scope
    alias = previous_image_alias(ctx) if capture_previous else None
    groups: dict[tuple[str, ...], list[tuple[RecordKey, dict[str, object]]]] = {}
    seen: set[str] = set()
    for record_id, data in updates:
        parsed_id = ctx.parse_primary_key(record_id)
        mutable, _sets, params = build_assignments(
            ctx, convert_record(ctx.columns, data), parsed_id
        )
        if not mutable:
            continue
        if str(parsed_id) in seen:
            return None
        seen.add(str(parsed_id))
        columns = tuple(sorted(mutable))
        values = {column: params[f"u_{column}"] for column in columns}
        groups.setdefault(columns, []).append((parsed_id, values))

    prepared: list[GroupedUpdate] = []
    for columns, rows in groups.items():
        sql = unnest_update_statement(
            ctx, list(columns), scoped_to_user=scoped, previous_alias=alias
        )
        if sql is None:
            return None
        for chunk in chunk_rows(rows, sized=False):
            params: dict[str, object] = {"c0": [row_id for row_id, _ in chunk]}
            for index, column in enumerate(columns):
                params[f"c{index + 1}"] = [values[column] for _, values in chunk]
            if scoped:
                params["current_user_id"] = str(user_id)
            prepared.append((sql, params, list(columns), alias, len(chunk)))
    return prepared


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
    grouped = prepare_grouped_updates(
        ctx,
        updates,
        user_id,
        enforce_user_scope=enforce_user_scope,
        capture_previous=event_factory is not None,
    )
    if grouped is None:
        grouped = [
            (sql, params, changed, alias, 1)
            for sql, params, changed, alias in prepare_bulk_updates(
                ctx,
                updates,
                user_id,
                enforce_user_scope=enforce_user_scope,
                capture_previous=event_factory is not None,
            )
        ]
    if not grouped:
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
            for sql, params, changed_columns, previous_alias, expected in grouped:
                result = await session.execute(text(sql), params)
                rows = result.fetchall()
                if len(rows) != expected:
                    # Rolls the whole batch back, which is the point: a partial
                    # write the caller cannot enumerate is worse than none.
                    raise DatastoreRecordNotFoundError(
                        "Record not found or update failed"
                    )
                updated += expected
                if event_factory is None:
                    continue
                for row in rows:
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
