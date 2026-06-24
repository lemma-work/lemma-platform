from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.datastore_entities import SYSTEM_COLUMNS
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreInfrastructureError,
    DatastoreQueryError,
    DatastoreRecordNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.ports import (
    DatastoreRecordRepositoryPort,
    DatastoreSchemaPort,
)
from app.modules.datastore.domain.record_entities import RecordEntity
from app.modules.datastore.infrastructure.db_error_parser import parse_db_error, raise_from_db_error
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier
from app.modules.datastore.services.record_validator import convert_record
from app.modules.datastore.services.table_context import TableContext


def _raise_record_write_error(
    exc: DBAPIError,
    *,
    operation: str,
    ctx: TableContext | None = None,
) -> None:
    """Map a write-path DB error into a clean, agent-readable domain error.

    Uses :mod:`db_error_parser` to detect check/FK/not-null/type/unique
    violations and produce focused messages with structured ``details`` (e.g.
    allowed ENUM values), instead of leaking the raw SQL + parameters.
    """
    raise_from_db_error(
        exc,
        table_name=ctx.table_name if ctx else None,
        columns=ctx.columns if ctx else None,
        operation=operation,
    )


def _raise_record_read_error(
    exc: DBAPIError,
    *,
    operation: str,
    table_name: str | None = None,
    columns: list | None = None,
) -> None:
    """Map a read-path DB error into a ``DatastoreQueryError`` (400) or
    ``DatastoreInfrastructureError`` (500), with a clean message + details.

    Read-path client errors (bad filter values, type mismatches) are always
    ``DATASTORE_QUERY_ERROR`` regardless of the underlying constraint type,
    because the caller sent a query, not a record to validate.
    """
    message, details, error_cls = parse_db_error(
        exc, table_name=table_name, columns=columns, operation=operation
    )
    if error_cls is DatastoreInfrastructureError:
        if details is not None:
            raise DatastoreInfrastructureError(message, details) from exc
        raise DatastoreInfrastructureError(message) from exc
    if details is not None:
        raise DatastoreQueryError(message, details) from exc
    raise DatastoreQueryError(message) from exc
from app.modules.datastore.services.value_converter import ValueConverter
from app.core.log.log import get_logger

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
    ) -> int:
        if not records:
            return 0

        prepared_records: list[dict[str, Any]] = []
        all_keys: set[str] = set()
        for record in records:
            converted = convert_record(ctx.columns, record, skip_auto=False)
            values = self._serialize_record_values(ctx, converted, user_id)
            prepared_records.append(values)
            all_keys.update(values.keys())

        ordered_keys: list[str] = []
        pk = ctx.primary_key_column
        if pk in all_keys:
            ordered_keys.append(pk)
        ordered_keys.extend(sorted(key for key in all_keys if key != pk))

        columns_sql = ", ".join(f'"{key}"' for key in ordered_keys)
        placeholders_sql = ", ".join(f":{key}" for key in ordered_keys)
        sql = (
            f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" '
            f'({columns_sql}) VALUES ({placeholders_sql})'
        )

        if upsert:
            update_columns = [
                key
                for key in ordered_keys
                if key not in {ctx.primary_key_column, "created_at"}
            ]
            set_clauses = [f'"{key}" = EXCLUDED."{key}"' for key in update_columns]
            set_clauses.append('"updated_at" = CURRENT_TIMESTAMP')
            sql += (
                f' ON CONFLICT ("{ctx.primary_key_column}") DO UPDATE SET '
                f'{", ".join(set_clauses)}'
            )

        statement = text(sql)
        params_list = [{key: record.get(key) for key in ordered_keys} for record in prepared_records]

        try:
            async with self.schema_manager.session_factory() as session:
                if ctx.enable_rls:
                    await self.schema_manager.set_rls_context(session, user_id)
                await session.execute(statement, params_list)
                await session.commit()
                return len(prepared_records)
        except DBAPIError as exc:
            logger.error("DB Error while bulk writing records: %s", exc)
            _raise_record_write_error(exc, operation="bulk write records", ctx=ctx)

    async def create_record(
        self,
        ctx: TableContext,
        data: dict[str, Any],
        user_id: UUID,
    ) -> RecordEntity:
        converted_data = convert_record(ctx.columns, data, skip_auto=False)

        columns: list[str] = []
        values = self._serialize_record_values(ctx, converted_data, user_id)
        placeholders: list[str] = []

        for key in values.keys():
            columns.append(f'"{key}"')
            placeholders.append(f":{key}")

        sql = (
            f'INSERT INTO "{ctx.schema_name}"."{ctx.table_name}" '
            f'({", ".join(columns)}) VALUES ({", ".join(placeholders)}) RETURNING *'
        )

        try:
            async with self.schema_manager.session_factory() as session:
                if ctx.enable_rls:
                    await self.schema_manager.set_rls_context(session, user_id)
                result = await session.execute(text(sql), values)
                row = result.fetchone()

                if not row:
                    raise DatastoreInfrastructureError("Failed to create record")

                await session.commit()
                return self._row_to_entity(dict(row._mapping), ctx)
        except DBAPIError as exc:
            logger.error("DB Error while creating record: %s", exc)
            _raise_record_write_error(exc, operation="create record", ctx=ctx)

    async def bulk_create_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
    ) -> int:
        return await self._bulk_write_records(ctx, records, user_id, upsert=False)

    async def bulk_upsert_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
    ) -> int:
        return await self._bulk_write_records(ctx, records, user_id, upsert=True)

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
            f'WHERE {" AND ".join(where_clauses)}'
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
    ) -> tuple[list[dict], int]:
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
                    {"ms": str(datastore_settings.datastore_query_statement_timeout_ms)},
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

                await self._reject_if_too_expensive(session, query)

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
                if len(rows) > max_rows:
                    rows = rows[:max_rows]
                return rows, len(rows)
        except DBAPIError as exc:
            logger.error("Query Error: %s", exc)
            _raise_record_read_error(exc, operation="query execution")

    async def _reject_if_too_expensive(self, session, query: str) -> None:
        """Reject a query whose planned cost or row estimate exceeds the ceiling.

        ``EXPLAIN`` (without ``ANALYZE``) only plans the statement, so this runs no
        user SQL; it executes under the same RLS context, so estimates reflect the
        row-filtered query.
        """
        try:
            explain = await session.execute(text(f"EXPLAIN (FORMAT JSON) {query}"))
            plan_json = explain.scalar_one()
        except DBAPIError as exc:
            logger.error("Query plan error: %s", exc)
            _raise_record_read_error(exc, operation="query planning")

        if isinstance(plan_json, str):
            plan_json = json.loads(plan_json)
        plan = plan_json[0]["Plan"]
        total_cost = float(plan.get("Total Cost", 0.0))
        plan_rows = int(plan.get("Plan Rows", 0))
        if (
            total_cost > datastore_settings.datastore_query_max_cost
            or plan_rows > datastore_settings.datastore_query_max_plan_rows
        ):
            raise DatastoreQueryError(
                "Query rejected: its estimated cost is too high. "
                "Add filters or a LIMIT to narrow the result set."
            )

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

                if op == "eq":
                    where_clauses.append(f'"{field}" = :{param_name}')
                elif op == "ne":
                    where_clauses.append(f'"{field}" != :{param_name}')
                elif op == "gt":
                    where_clauses.append(f'"{field}" > :{param_name}')
                elif op == "gte":
                    where_clauses.append(f'"{field}" >= :{param_name}')
                elif op == "lt":
                    where_clauses.append(f'"{field}" < :{param_name}')
                elif op == "lte":
                    where_clauses.append(f'"{field}" <= :{param_name}')
                elif op == "like":
                    where_clauses.append(f'"{field}" LIKE :{param_name}')
                elif op == "ilike":
                    where_clauses.append(f'"{field}" ILIKE :{param_name}')
                else:
                    raise DatastoreValidationError(
                        f"Unsupported filter operator '{op}'",
                        details={
                            "operator": op,
                            "allowed_operators": [
                                "eq", "ne", "gt", "gte", "lt", "lte", "like", "ilike",
                            ],
                        },
                    )

                if col:
                    try:
                        value = ValueConverter.convert_value(value, col)
                    except ValueError:
                        pass
                params[param_name] = value

        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
            count_sql += where_sql
            list_sql += where_sql

        default_sort_uses_created_at = False
        if sorts:
            clauses: list[str] = []
            for field, direction in sorts:
                self._sanitize_identifier(field)
                order_dir = "DESC" if direction.lower() == "desc" else "ASC"
                clauses.append(f'"{field}" {order_dir}')
            list_sql += " ORDER BY " + ", ".join(clauses)
        else:
            default_sort_uses_created_at = any(c.name == "created_at" for c in ctx.columns)
            list_sql += (
                ' ORDER BY "created_at" DESC'
                if default_sort_uses_created_at
                else f' ORDER BY "{ctx.primary_key_column}" DESC'
            )

        list_sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = limit
        params["offset"] = offset

        try:
            async with self.schema_manager.session_factory() as session:
                if ctx.enable_rls:
                    await self.schema_manager.set_rls_context(
                        session,
                        user_id,
                        is_pod_admin=not enforce_user_scope,
                    )

                count_result = await session.execute(text(count_sql), params)
                total = count_result.scalar() or 0

                # The ORDER BY only references created_at when it is present in
                # ctx.columns (see above), which mirrors the physical schema, so
                # no defensive retry is needed.
                list_result = await session.execute(text(list_sql), params)
                rows = list_result.fetchall()

                return [self._row_to_entity(dict(row._mapping), ctx) for row in rows], total
        except DBAPIError as exc:
            logger.error("List records error: %s", exc)
            _raise_record_read_error(
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
    ) -> RecordEntity:
        parsed_id = ctx.parse_primary_key(record_id)
        converted_data = convert_record(ctx.columns, data)
        mutable_data = {
            key: value
            for key, value in converted_data.items()
            if key not in SYSTEM_COLUMNS and key != ctx.primary_key_column
        }

        if not mutable_data:
            return await self.get_record(
                ctx,
                parsed_id,
                user_id,
                enforce_user_scope=enforce_user_scope,
            )

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": parsed_id}
        column_map = {col.name: col for col in ctx.columns}

        for key, value in mutable_data.items():
            self._sanitize_identifier(key)
            param_name = f"u_{key}"
            set_clauses.append(f'"{key}" = :{param_name}')
            if key in column_map:
                params[param_name] = ValueConverter.serialize_for_sql(
                    value, column_map[key]
                )
            else:
                params[param_name] = value

        set_clauses.append('"updated_at" = CURRENT_TIMESTAMP')

        where_clauses = [f'"{ctx.primary_key_column}" = :id']
        self._apply_current_user_scope(
            ctx,
            where_clauses,
            params,
            user_id,
            enforce_user_scope=enforce_user_scope,
        )

        sql = (
            f'UPDATE "{ctx.schema_name}"."{ctx.table_name}" SET {", ".join(set_clauses)} '
            f'WHERE {" AND ".join(where_clauses)} RETURNING *'
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
                    raise DatastoreRecordNotFoundError("Record not found or update failed")

                await session.commit()
                return self._row_to_entity(dict(row._mapping), ctx)
        except DBAPIError as exc:
            _raise_record_write_error(exc, operation="update record", ctx=ctx)

    async def delete_record(
        self,
        ctx: TableContext,
        record_id: Any,
        user_id: UUID,
        *,
        enforce_user_scope: bool = True,
    ) -> bool:
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
            f'WHERE {" AND ".join(where_clauses)}'
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
                if result.rowcount == 0:
                    raise DatastoreRecordNotFoundError()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DatastoreConflictError(
                    "Cannot delete: this record is still referenced by other "
                    "records. Remove or reassign those first."
                ) from exc
            return True
