"""Single and bulk record writes that append to the pod's change log.

The change-log twin of the repository's create, update, delete and bulk
create/upsert: the same statements, built by the same builders, each wrapped
by ``ChangeCapture`` so the write and its changes are one statement in one
transaction. Kept apart from ``record_repository`` (already at the size the
architecture gate allows) and from the outbox path, which stays until every
pod schema carries the log and is then removed.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.log.log import get_logger
from app.modules.datastore.changes.capture import ChangeCapture
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreInfrastructureError,
    DatastoreRecordNotFoundError,
)
from app.modules.datastore.domain.record_entities import RecordEntity
from app.modules.datastore.domain.row_security import RowPrincipal
from app.modules.datastore.infrastructure.record_errors import (
    raise_record_write_error,
)
from app.modules.datastore.infrastructure.record_optimistic_lock import (
    apply_expected_updated_at,
    primary_key_scope,
    raise_missing_or_stale,
)
from app.modules.datastore.infrastructure.record_update_sql import (
    build_assignments,
    build_bulk_statements,
    build_update_statement,
    bulk_conflict_clause,
    order_bulk_keys,
    split_previous_image,
)
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.services.table_context import TableContext

if TYPE_CHECKING:
    from app.modules.datastore.infrastructure.record_repository import (
        DatastoreRecordRepository,
    )

logger = get_logger(__name__)

type RecordValues = dict[str, object]


async def create_logged(
    repo: DatastoreRecordRepository,
    ctx: TableContext,
    data: RecordValues,
    user_id: UUID,
    changes: ChangeCapture,
) -> RecordEntity:
    converted = convert_record(ctx.columns, dict(data), skip_auto=False)
    values = repo._serialize_record_values(ctx, converted, user_id)
    columns = ", ".join(f'"{key}"' for key in values)
    placeholders = ", ".join(f":{key}" for key in values)
    sql = (
        f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" '
        f"({columns}) VALUES ({placeholders}) RETURNING *"
    )
    await changes.prepare()
    try:
        async with repo.schema_manager.session_factory() as session:
            if ctx.enable_rls:
                await repo.schema_manager.set_rls_context(
                    session, RowPrincipal.for_user(user_id)
                )
            await changes.begin(session)
            statement, params = changes.wrap(sql, dict(values), op="I")
            row = (await session.execute(text(statement), params)).fetchone()
            if row is None:
                raise DatastoreInfrastructureError("Failed to create record")
            entity = repo._row_to_entity(changes.row(row), ctx)
            await changes.wake_handoff(session)
            await session.commit()
            return entity
    except DBAPIError as exc:
        logger.debug("datastore.record.create.propagated", exc_info=True)
        raise_record_write_error(exc, operation="create record", ctx=ctx)


async def update_logged(
    repo: DatastoreRecordRepository,
    ctx: TableContext,
    record_id: object,
    data: RecordValues,
    user_id: UUID,
    changes: ChangeCapture,
    *,
    enforce_user_scope: bool,
    expected_updated_at: datetime | None,
) -> RecordEntity:
    parsed_id = ctx.parse_primary_key(record_id)
    mutable, set_clauses, params = build_assignments(
        ctx, convert_record(ctx.columns, dict(data)), parsed_id
    )
    if not mutable:
        return await repo.get_record(
            ctx, parsed_id, user_id, enforce_user_scope=enforce_user_scope
        )
    where = [f'"{ctx.primary_key_column}" = :id']
    repo._apply_current_user_scope(
        ctx, where, params, user_id, enforce_user_scope=enforce_user_scope
    )
    if expected_updated_at is not None:
        apply_expected_updated_at(ctx, where, params, expected_updated_at)
    changed = sorted(mutable)
    await changes.prepare()
    try:
        async with repo.schema_manager.session_factory() as session:
            if ctx.enable_rls:
                await repo.schema_manager.set_rls_context(
                    session,
                    RowPrincipal.for_user(user_id, is_pod_admin=not enforce_user_scope),
                )
            await changes.begin(session)
            # The prior image is captured only for an update a subscriber reads:
            # it costs a locked self-join that nobody else has a use for.
            sql, alias = build_update_statement(
                ctx,
                set_clauses=set_clauses,
                where_clauses=where,
                capture_previous="U" in changes.snapshot,
            )
            statement, bound = changes.wrap(
                sql, params, op="U", previous_alias=alias, changed=changed
            )
            row = (await session.execute(text(statement), bound)).fetchone()
            if row is None:
                if expected_updated_at is not None:
                    scope, scope_params = primary_key_scope(
                        ctx, parsed_id, user_id, enforce_user_scope=enforce_user_scope
                    )
                    await raise_missing_or_stale(
                        session, ctx, where_clauses=scope, params=scope_params
                    )
                raise DatastoreRecordNotFoundError("Record not found or update failed")
            mapping = changes.row(row)
            split_previous_image(mapping, alias, changed)
            entity = repo._row_to_entity(mapping, ctx)
            await changes.wake_handoff(session)
            await session.commit()
            return entity
    except DBAPIError as exc:
        raise_record_write_error(exc, operation="update record", ctx=ctx)


async def delete_logged(
    repo: DatastoreRecordRepository,
    ctx: TableContext,
    record_id: object,
    user_id: UUID,
    changes: ChangeCapture,
    *,
    enforce_user_scope: bool,
) -> RecordEntity:
    parsed_id = ctx.parse_primary_key(record_id)
    where = [f'"{ctx.primary_key_column}" = :id']
    params: dict[str, object] = {"id": parsed_id}
    repo._apply_current_user_scope(
        ctx, where, params, user_id, enforce_user_scope=enforce_user_scope
    )
    sql = (
        f'DELETE FROM "{ctx.schema_name}"."{ctx.table_name}" '
        f"WHERE {' AND '.join(where)} RETURNING *"
    )
    await changes.prepare()
    async with repo.schema_manager.session_factory() as session:
        if ctx.enable_rls:
            await repo.schema_manager.set_rls_context(
                session,
                RowPrincipal.for_user(user_id, is_pod_admin=not enforce_user_scope),
            )
        try:
            await changes.begin(session)
            statement, bound = changes.wrap(sql, params, op="D")
            row = (await session.execute(text(statement), bound)).fetchone()
            if row is None:
                raise DatastoreRecordNotFoundError()
            entity = repo._row_to_entity(changes.row(row), ctx)
            await changes.wake_handoff(session)
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DatastoreConflictError(
                "Cannot delete: this record is still referenced by other "
                "records. Remove or reassign those first."
            ) from exc
        return entity


async def bulk_write_logged(
    repo: DatastoreRecordRepository,
    ctx: TableContext,
    records: list[RecordValues],
    user_id: UUID,
    changes: ChangeCapture,
    *,
    upsert: bool,
) -> int:
    """Bulk create or upsert, set-based: one fixed-shape statement per chunk.

    The rows only need counting, so each statement returns a count, and the
    changes are appended inside it -- an upsert's rows each as an insert or an
    update, by whether the statement inserted them.
    """
    if not records:
        return 0
    prepared: list[RecordValues] = []
    keys: set[str] = set()
    for record in records:
        converted = convert_record(ctx.columns, dict(record), skip_auto=False)
        values = repo._serialize_record_values(ctx, converted, user_id)
        prepared.append(values)
        keys.update(values)
    ordered = order_bulk_keys(ctx.primary_key_column, keys)
    conflict = bulk_conflict_clause(ctx, ordered) if upsert else ""
    statements = build_bulk_statements(ctx, ordered, prepared, conflict)
    await changes.prepare()
    try:
        async with repo.schema_manager.session_factory() as session:
            if ctx.enable_rls:
                await repo.schema_manager.set_rls_context(
                    session, RowPrincipal.for_user(user_id)
                )
            await changes.begin(session)
            written = 0
            for sql, params in statements:
                statement, bound = changes.wrap(
                    sql, params, op=None if upsert else "I", returning="count"
                )
                written += changes.count(await session.execute(text(statement), bound))
            await changes.wake_handoff(session)
            await session.commit()
    except DBAPIError as exc:
        logger.debug("datastore.record.bulk_write.propagated", exc_info=True)
        raise_record_write_error(exc, operation="bulk write records", ctx=ctx)
    return written


__all__ = ["bulk_write_logged", "create_logged", "delete_logged", "update_logged"]
