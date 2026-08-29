from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreInfrastructureError,
    DatastoreRecordNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.ports import (
    DatastoreRecordRepositoryPort,
    DatastoreSchemaPort,
    RecordEventFactory,
)
from app.modules.datastore.domain.record_entities import RecordEntity
from app.modules.datastore.infrastructure.record_errors import (
    raise_record_read_error,
    raise_record_write_error,
)
from app.modules.datastore.infrastructure.record_filter_sql import (
    build_filter_predicate,
)
from app.modules.datastore.infrastructure.record_page import rows_and_total
from app.modules.datastore.infrastructure.record_update_sql import (
    build_assignments,
    build_update_statement,
    bulk_conflict_clause,
    bulk_insert_statement,
    build_bulk_statements,
    order_bulk_keys,
    split_previous_image,
)
from app.modules.datastore.infrastructure.rls_context import verify_rls_context
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.infrastructure.record_indexes import ensure_listing_index_for
from app.modules.datastore.infrastructure.record_query_cost import (
    reject_if_too_expensive,
)
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.value_converter import ValueConverter
from app.core.log.log import get_logger
from app.core.domain.events import DomainEvent
from app.modules.datastore.infrastructure.transactional_events import (
    ensure_datastore_event_outbox,
    stage_domain_events,
)

logger = get_logger(__name__)


class DatastoreRecordRepository(DatastoreRecordRepositoryPort):
    def __init__(self, schema_manager: DatastoreSchemaPort):
        self.schema_manager = schema_manager

    def _sanitize_identifier(self, identifier: str) -> str:
        return sanitize_identifier(identifier)

    def _row_to_entity(self, row: dict[str, Any], ctx: TableContext) -> RecordEntity:
        data = ValueConverter.deserialize_record(row, ctx.columns)

        return RecordEntity(
            id=data.get("id"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            pod_id=ctx.pod_id,
            table_name=ctx.table_name,
            data=data,
            user_id=UUID(str(data.get("user_id"))) if data.get("user_id") else None,
        )

    def _apply_current_user_scope(
        self,
        ctx: TableContext,
        where_clauses: list[str],
        params: dict[str, Any],
        user_id: UUID,
        *,
        enforce_user_scope: bool,
    ) -> None:
        if not ctx.enable_rls or not enforce_user_scope:
            return
        where_clauses.append('"user_id" = :current_user_id')
        params["current_user_id"] = str(user_id)

    def _serialize_record_values(
        self,
        ctx: TableContext,
        converted_data: dict[str, Any],
        user_id: UUID,
    ) -> dict[str, Any]:
        caller_user_id = str(user_id)

        if ctx.enable_rls:
            provided_user_id = converted_data.get("user_id")
            if provided_user_id is None:
                converted_data["user_id"] = caller_user_id
            elif str(provided_user_id) != caller_user_id:
                raise DatastoreValidationError(
                    "user_id must match the current user for RLS-enabled tables"
                )

        values: dict[str, Any] = {}
        column_map = {col.name: col for col in ctx.columns}
        for key, value in converted_data.items():
            self._sanitize_identifier(key)
            if key in column_map:
                values[key] = ValueConverter.serialize_for_sql(value, column_map[key])
            else:
                values[key] = value

        return values

    async def _bulk_write_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
        *,
        upsert: bool,
        event_factory: RecordEventFactory | None = None,
    ) -> int:
        if not records:
            return 0
        await ensure_datastore_event_outbox()

        prepared_records: list[dict[str, Any]] = []
        all_keys: set[str] = set()
        for record in records:
            converted = convert_record(ctx.columns, record, skip_auto=False)
            values = self._serialize_record_values(ctx, converted, user_id)
            prepared_records.append(values)
            all_keys.update(values.keys())

        ordered_keys = order_bulk_keys(ctx.primary_key_column, all_keys)
        conflict_sql = bulk_conflict_clause(ctx, ordered_keys) if upsert else ""
        statements = (
            build_bulk_statements(ctx, ordered_keys, prepared_records, conflict_sql)
            if event_factory is not None
            else []
        )

        try:
            async with self.schema_manager.session_factory() as session:
                if ctx.enable_rls:
                    await self.schema_manager.set_rls_context(session, user_id)

                if event_factory is None:
                    # No subscriber: executemany is cheapest, nothing needs the rows.
                    await session.execute(
                        text(bulk_insert_statement(ctx, ordered_keys) + conflict_sql),
                        [
                            {key: record.get(key) for key in ordered_keys}
                            for record in prepared_records
                        ],
                    )
                else:
                    # Convert each chunk's rows right after its own execute.
                    #
                    # Batching every conversion until after the last statement
                    # looks tidier and is measurably worse: the events have to
                    # be staged in this transaction for the outbox to be atomic,
                    # so the work cannot leave it, and collecting it into one
                    # stretch turns N short gaps into a single long one. Tried
                    # exactly that, and the worst gap on this line went from
                    # 784ms to 2163ms -- with row locks held throughout. The
                    # metric that matters is the longest contiguous gap, not the
                    # number of them.
                    events: list[DomainEvent] = []
                    for sql, params in statements:
                        result = await session.execute(text(sql), params)
                        events.extend(
                            event_factory(self._row_to_entity(dict(row._mapping), ctx))
                            for row in result.fetchall()
                        )
                    await stage_domain_events(session, events)

                await session.commit()
                return len(prepared_records)
        except DBAPIError as exc:
            logger.debug("datastore.record.bulk_write.propagated", exc_info=True)
            raise_record_write_error(exc, operation="bulk write records", ctx=ctx)

    async def create_record(
        self,
        ctx: TableContext,
        data: dict[str, Any],
        user_id: UUID,
        *,
        event_factory: RecordEventFactory | None = None,
    ) -> RecordEntity:
        converted_data = convert_record(ctx.columns, data, skip_auto=False)
        if event_factory is not None:
            await ensure_datastore_event_outbox()

        columns: list[str] = []
        values = self._serialize_record_values(ctx, converted_data, user_id)
        placeholders: list[str] = []

        for key in values.keys():
            columns.append(f'"{key}"')
            placeholders.append(f":{key}")

        sql = (
            f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" '
            f"({', '.join(columns)}) VALUES ({', '.join(placeholders)}) RETURNING *"
        )

        try:
            async with self.schema_manager.session_factory() as session:
                if ctx.enable_rls:
                    await self.schema_manager.set_rls_context(session, user_id)
                result = await session.execute(text(sql), values)
                row = result.fetchone()

                if not row:
                    raise DatastoreInfrastructureError("Failed to create record")

                entity = self._row_to_entity(dict(row._mapping), ctx)
                if event_factory is not None:
                    await stage_domain_events(session, [event_factory(entity)])
                await session.commit()
                return entity
        except DBAPIError as exc:
            logger.debug("datastore.record.create.propagated", exc_info=True)
            raise_record_write_error(exc, operation="create record", ctx=ctx)

    async def bulk_create_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
        *,
        event_factory: RecordEventFactory | None = None,
    ) -> int:
        return await self._bulk_write_records(
            ctx, records, user_id, upsert=False, event_factory=event_factory
        )

    async def bulk_upsert_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
        *,
        event_factory: RecordEventFactory | None = None,
    ) -> int:
        return await self._bulk_write_records(
            ctx, records, user_id, upsert=True, event_factory=event_factory
        )

    async def get_record(
        self,
        ctx: TableContext,
        record_id: Any,
        user_id: UUID,
        *,
        enforce_user_scope: bool = True,
    ) -> RecordEntity:
        parsed_id = ctx.parse_primary_key(record_id)
        where_clauses = [f'"{ctx.primary_key_column}" = :id']
        params: dict[str, Any] = {"id": parsed_id}
        self._apply_current_user_scope(
            ctx,
            where_clauses,
            params,
            user_id,
            enforce_user_scope=enforce_user_scope,
        )
        sql = (
            f'SELECT * FROM "{ctx.schema_name}"."{ctx.table_name}" '
            f"WHERE {' AND '.join(where_clauses)}"
        )

        async with self.schema_manager.session_factory() as session:
            if ctx.enable_rls:
                await self.schema_manager.set_rls_context(
                    session,
                    user_id,
                    is_pod_admin=not enforce_user_scope,
                )
            result = await session.execute(text(sql), params)
            row = result.fetchone()

        if not row:
            raise DatastoreRecordNotFoundError()
        return self._row_to_entity(dict(row._mapping), ctx)

    async def execute_readonly_query(
        self,
        pod_id: UUID,
        query: str,
        user_id: UUID,
        enable_rls: bool = True,
        is_pod_admin: bool = False,
    ) -> tuple[list[dict], int, bool]:
        """Execute a pre-validated read-only SQL query inside the pod schema.

        Callers must validate the statement (single, read-only, no cross-schema
        references) via ``analyze_query`` first; this method enforces the runtime
        guards: a read-only transaction, a per-statement timeout, an EXPLAIN-based
        cost ceiling that rejects database-hogging queries before they run, and a
        streamed row cap so a large result never fully materializes.

        ``is_pod_admin`` is forwarded to the RLS context: when true, RLS-enabled
        tables return all rows; otherwise rows are scoped to ``user_id``.
        """
        max_rows = datastore_settings.datastore_query_max_rows
        query_role = sanitize_identifier(datastore_settings.datastore_query_role)
        try:
            async with self.schema_manager.session_factory() as session:
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(
                    text("SELECT set_config('statement_timeout', :ms, true)"),
                    {
                        "ms": str(
                            datastore_settings.datastore_query_statement_timeout_ms
                        )
                    },
                )

                schema_name = self.schema_manager.get_schema_name(pod_id)
                # All SETs are transaction-local so nothing leaks back to the pool.
                await session.execute(text(f'SET LOCAL search_path TO "{schema_name}"'))

                if enable_rls:
                    await self.schema_manager.set_rls_context(
                        session, user_id, is_pod_admin=is_pod_admin
                    )

                # Run the user's SQL as the non-superuser, NOBYPASSRLS role so RLS
                # policies are enforced (the app's own connection bypasses RLS).
                # Set after the RLS-context GUCs above, which the policies read.
                await session.execute(text(f'SET LOCAL ROLE "{query_role}"'))

                await reject_if_too_expensive(session, query)

                # Stream via a server-side cursor and pull at most max_rows + 1 so a
                # runaway result set never fully materializes in memory; the extra
                # row only tells us the result was truncated.
                result = await session.stream(text(query))
                rows: list[dict] = []
                async for row in result:
                    rows.append(dict(row._mapping))
                    if len(rows) > max_rows:
                        break
                await result.close()
                # The extra row is the only evidence the result was cut, and it
                # used to be dropped here -- so a caller was handed exactly
                # `max_rows` rows and a count equal to them, which reads as a
                # complete result. An agent then reports "you have 1000 orders"
                # to someone with forty thousand.
                truncated = len(rows) > max_rows
                if truncated:
                    rows = rows[:max_rows]
                if enable_rls:
                    await verify_rls_context(
                        session, user_id, is_pod_admin=is_pod_admin
                    )
                return rows, len(rows), truncated
        except DBAPIError as exc:
            logger.debug("datastore.record.query.propagated", exc_info=True)
            raise_record_read_error(exc, operation="query execution")

    async def ensure_listing_index(self, ctx: TableContext) -> None:
        """Back-fill the listing index for one table, lazily."""
        await ensure_listing_index_for(self.schema_manager, ctx)

    async def list_records(
        self,
        ctx: TableContext,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        sorts: list[tuple[str, str]] | None = None,
        filters: list[tuple[str, str, Any]] | None = None,
        *,
        enforce_user_scope: bool = True,
    ) -> tuple[list[RecordEntity], int]:
        # Tables predating the listing index have none, and this is the read
        # whose sort it matches. See ``SchemaManager.ensure_record_index``.
        await self.ensure_listing_index(ctx)
        count_sql = f'SELECT COUNT(*) FROM "{ctx.schema_name}"."{ctx.table_name}"'
        list_sql = f'SELECT * FROM "{ctx.schema_name}"."{ctx.table_name}"'

        where_clauses: list[str] = []
        params: dict[str, Any] = {}

        self._apply_current_user_scope(
            ctx,
            where_clauses,
            params,
            user_id,
            enforce_user_scope=enforce_user_scope,
        )

        if filters:
            for field, op, value in filters:
                self._sanitize_identifier(field)
                col = next((c for c in ctx.columns if c.name == field), None)
                param_name = f"f_{len(params)}"

                clause, bound = build_filter_predicate(
                    field, op, value, col, param_name
                )
                where_clauses.append(clause)
                params.update(bound)

        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
            count_sql += where_sql
            list_sql += where_sql

        if sorts:
            clauses: list[str] = []
            for field, direction in sorts:
                self._sanitize_identifier(field)
                order_dir = "DESC" if direction.lower() == "desc" else "ASC"
                clauses.append(f'"{field}" {order_dir}')
            list_sql += " ORDER BY " + ", ".join(clauses)
        else:
            # pk breaks the tie; `created_at` alone can repeat/drop rows.
            list_sql += (
                f' ORDER BY "created_at" DESC, "{ctx.primary_key_column}" DESC'
                if any(c.name == "created_at" for c in ctx.columns)
                else f' ORDER BY "{ctx.primary_key_column}" DESC'
            )

        list_sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = limit + 1  # +1: `rows_and_total` skips the COUNT
        params["offset"] = offset

        try:
            async with self.schema_manager.session_factory() as session:
                if ctx.enable_rls:
                    await self.schema_manager.set_rls_context(
                        session,
                        user_id,
                        is_pod_admin=not enforce_user_scope,
                    )

                rows, total = await rows_and_total(
                    session,
                    list_sql=list_sql,
                    count_sql=count_sql,
                    params=params,
                    limit=limit,
                    offset=offset,
                )

                return [
                    self._row_to_entity(dict(row._mapping), ctx) for row in rows
                ], total
        except DBAPIError as exc:
            logger.debug("datastore.record.list.propagated", exc_info=True)
            raise_record_read_error(
                exc,
                operation="list records",
                table_name=ctx.table_name,
                columns=ctx.columns,
            )

    async def update_record(
        self,
        ctx: TableContext,
        record_id: Any,
        data: dict[str, Any],
        user_id: UUID,
        *,
        enforce_user_scope: bool = True,
        event_factory: RecordEventFactory | None = None,
    ) -> RecordEntity:
        if event_factory is not None:
            await ensure_datastore_event_outbox()
        parsed_id = ctx.parse_primary_key(record_id)
        mutable_data, set_clauses, params = build_assignments(
            ctx, convert_record(ctx.columns, data), parsed_id
        )

        if not mutable_data:
            return await self.get_record(
                ctx,
                parsed_id,
                user_id,
                enforce_user_scope=enforce_user_scope,
            )

        where_clauses = [f'"{ctx.primary_key_column}" = :id']
        self._apply_current_user_scope(
            ctx,
            where_clauses,
            params,
            user_id,
            enforce_user_scope=enforce_user_scope,
        )

        changed_columns = sorted(mutable_data.keys())
        sql, previous_alias = build_update_statement(
            ctx,
            set_clauses=set_clauses,
            where_clauses=where_clauses,
            capture_previous=event_factory is not None,
        )

        try:
            async with self.schema_manager.session_factory() as session:
                if ctx.enable_rls:
                    await self.schema_manager.set_rls_context(
                        session,
                        user_id,
                        is_pod_admin=not enforce_user_scope,
                    )

                result = await session.execute(text(sql), params)
                row = result.fetchone()
                if not row:
                    raise DatastoreRecordNotFoundError(
                        "Record not found or update failed"
                    )

                row_mapping = dict(row._mapping)
                previous = split_previous_image(
                    row_mapping, previous_alias, changed_columns
                )
                entity = self._row_to_entity(row_mapping, ctx)
                if event_factory is not None:
                    await stage_domain_events(
                        session, [event_factory(entity, changed_columns, previous)]
                    )
                await session.commit()
                return entity
        except DBAPIError as exc:
            raise_record_write_error(exc, operation="update record", ctx=ctx)

    async def delete_record(
        self,
        ctx: TableContext,
        record_id: Any,
        user_id: UUID,
        *,
        enforce_user_scope: bool = True,
        event_factory: RecordEventFactory | None = None,
    ) -> RecordEntity:
        if event_factory is not None:
            await ensure_datastore_event_outbox()
        parsed_id = ctx.parse_primary_key(record_id)
        where_clauses = [f'"{ctx.primary_key_column}" = :id']
        params: dict[str, Any] = {"id": parsed_id}
        self._apply_current_user_scope(
            ctx,
            where_clauses,
            params,
            user_id,
            enforce_user_scope=enforce_user_scope,
        )
        sql = (
            f'DELETE FROM "{ctx.schema_name}"."{ctx.table_name}" '
            f"WHERE {' AND '.join(where_clauses)} RETURNING *"
        )

        async with self.schema_manager.session_factory() as session:
            if ctx.enable_rls:
                await self.schema_manager.set_rls_context(
                    session,
                    user_id,
                    is_pod_admin=not enforce_user_scope,
                )
            try:
                result = await session.execute(text(sql), params)
                row = result.fetchone()
                if row is None:
                    raise DatastoreRecordNotFoundError()
                entity = self._row_to_entity(dict(row._mapping), ctx)
                if event_factory is not None:
                    await stage_domain_events(session, [event_factory(entity)])
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DatastoreConflictError(
                    "Cannot delete: this record is still referenced by other "
                    "records. Remove or reassign those first."
                ) from exc
            return entity
