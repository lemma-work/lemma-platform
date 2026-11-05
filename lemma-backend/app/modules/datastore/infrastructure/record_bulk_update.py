"""Run many prepared record updates inside one transaction.

`update_record` is right for one row and wrong N times: it opens its own
session, sets the RLS context, runs the UPDATE, appends its change and commits.
A bulk update therefore paid four round trips *and a fresh connection checkout*
per row, while bulk create and upsert had been doing the batched thing all
along. Production measured the difference on
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
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.changes.capture import ChangeCapture
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreRecordNotFoundError
from app.modules.datastore.infrastructure.record_bulk_sql import (
    chunk_rows,
    unnest_update_statement,
)
from app.modules.datastore.infrastructure.record_update_sql import (
    build_assignments,
    previous_image_alias,
    prepare_bulk_updates,
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
    changes: ChangeCapture,
    enforce_user_scope: bool = True,
) -> int:
    """Apply many updates in one transaction, like create and upsert do.

    A free function rather than a repository method: this is a cohesive unit
    that does not need the class.
    """
    if not updates:
        return 0
    grouped = prepare_grouped_updates(
        ctx,
        updates,
        user_id,
        enforce_user_scope=enforce_user_scope,
        capture_previous=False,
    )
    if grouped is None:
        grouped = [
            (sql, params, changed, alias, 1)
            for sql, params, changed, alias in prepare_bulk_updates(
                ctx,
                updates,
                user_id,
                enforce_user_scope=enforce_user_scope,
                capture_previous=False,
            )
        ]
    if not grouped:
        return 0
    return await _bulk_update_logged(
        repository,
        ctx,
        updates,
        user_id,
        grouped,
        changes,
        enforce_user_scope=enforce_user_scope,
    )


async def _bulk_update_logged(
    repository: Any,
    ctx: TableContext,
    updates: list[tuple[Any, dict[str, Any]]],
    user_id: UUID,
    grouped: list[GroupedUpdate],
    changes: ChangeCapture,
    *,
    enforce_user_scope: bool,
) -> int:
    """The same statements, each appending its changes; see ``ChangeCapture``.

    ``grouped`` was built without a prior image. A watched table needs one,
    so its statements are rebuilt with it once the probe says so -- the rare
    case pays the second build, the common one never does.
    """
    schema_manager = repository.schema_manager
    await changes.prepare()
    try:
        async with schema_manager.session_factory() as session:
            principal = ctx.row_principal(user_id, is_pod_admin=not enforce_user_scope)
            if principal is not None:
                await schema_manager.set_rls_context(session, principal)
            await changes.begin(session)
            if "U" in changes.snapshot:
                grouped = _with_prior_image(
                    ctx, updates, user_id, enforce_user_scope=enforce_user_scope
                )
            updated = 0
            for sql, params, changed_columns, alias, expected in grouped:
                statement, bound = changes.wrap(
                    sql,
                    params,
                    op="U",
                    previous_alias=alias,
                    changed=changed_columns,
                    returning="count",
                )
                count = changes.count(await session.execute(text(statement), bound))
                if count != expected:
                    raise DatastoreRecordNotFoundError(
                        "Record not found or update failed"
                    )
                updated += expected
            await changes.wake_handoff(session)
            await session.commit()
            return updated
    except DBAPIError as exc:
        logger.debug("datastore.record.bulk_update.propagated", exc_info=True)
        raise_record_write_error(exc, operation="bulk update records", ctx=ctx)


def _with_prior_image(
    ctx: TableContext,
    updates: list[tuple[Any, dict[str, Any]]],
    user_id: UUID,
    *,
    enforce_user_scope: bool,
) -> list[GroupedUpdate]:
    grouped = prepare_grouped_updates(
        ctx,
        updates,
        user_id,
        enforce_user_scope=enforce_user_scope,
        capture_previous=True,
    )
    if grouped is not None:
        return grouped
    return [
        (sql, params, changed, alias, 1)
        for sql, params, changed, alias in prepare_bulk_updates(
            ctx,
            updates,
            user_id,
            enforce_user_scope=enforce_user_scope,
            capture_previous=True,
        )
    ]
