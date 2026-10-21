from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.domain.row_security import RowPrincipal
from app.modules.datastore.domain.errors import (
    DatastoreRecordNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.ports import (
    DatastoreRecordRepositoryPort,
    DatastoreSchemaPort,
)
from app.modules.datastore.domain.record_entities import RecordEntity
from app.modules.datastore.infrastructure.record_errors import (
    raise_record_read_error,
)
from app.modules.datastore.infrastructure.record_filter_sql import (
    build_filter_predicate,
)
from app.modules.datastore.infrastructure.record_page import (
    order_by_clause,
    rows_and_total,
)
from app.modules.datastore.infrastructure.readonly_query import (
    QueryRows,
    execute_readonly_query,
)
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier
from app.modules.datastore.infrastructure.record_indexes import ensure_listing_index_for
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.value_converter import ValueConverter
from app.core.log.log import get_logger
from app.modules.datastore.infrastructure import record_logged_writes as logged
from app.modules.datastore.changes.capture import ChangeCapture

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
        user_id: UUID | None,
        *,
        enforce_user_scope: bool,
    ) -> None:
        if not ctx.enable_rls or not enforce_user_scope:
            return
        if user_id is None:
            # Nobody -- an outsider, another pod asking over a link -- owns no
            # row of a per-user table: an empty page, not a cast of "None".
            where_clauses.append("FALSE")
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

    # Every write appends its changes to the pod's change log in the same
    # statement (``ChangeCapture``, ``record_logged_writes``): there is no
    # other write path.

    async def create_record(
        self,
        ctx: TableContext,
        data: dict[str, Any],
        user_id: UUID,
        *,
        changes: ChangeCapture,
        existing_on_conflict: bool = False,
    ) -> RecordEntity:
        """Insert one row; ``existing_on_conflict`` returns the row a repeat hits.

        With it, a create whose primary key is already taken is not an error:
        it is the same create again (``services/record_idempotency``), and the
        stored row comes back with no second change.
        """
        return await logged.create_logged(
            self,
            ctx,
            data,
            user_id,
            changes,
            existing_on_conflict=existing_on_conflict,
        )

    async def bulk_create_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
        *,
        changes: ChangeCapture,
    ) -> int:
        return await logged.bulk_write_logged(
            self, ctx, records, user_id, changes, upsert=False
        )

    async def bulk_upsert_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
        *,
        changes: ChangeCapture,
    ) -> int:
        return await logged.bulk_write_logged(
            self, ctx, records, user_id, changes, upsert=True
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
            principal = ctx.row_principal(user_id, is_pod_admin=not enforce_user_scope)
            if principal is not None:
                await self.schema_manager.set_rls_context(session, principal)
            result = await session.execute(text(sql), params)
            row = result.fetchone()

        if not row:
            raise DatastoreRecordNotFoundError()
        return self._row_to_entity(dict(row._mapping), ctx)

    async def execute_readonly_query(
        self, pod_id: UUID, query: str, principal: RowPrincipal
    ) -> QueryRows:
        """Execute a pre-validated read-only SQL query inside the pod schema.

        Callers must validate the statement (single, read-only, no cross-schema
        references) via ``analyze_query`` first; this method enforces the runtime
        guards: a read-only transaction, a per-statement timeout, an EXPLAIN-based
        cost ceiling that rejects database-hogging queries before they run, and a
        streamed row cap so a large result never fully materializes.

        ``principal`` is who the row policies see: a member (every row of a
        contact-owned table; their own, or all as a pod admin, of a per-user
        one), a contact (their own contact-owned rows only), or an outsider
        (neither). The query itself runs in ``readonly_query``.
        """
        try:
            return await execute_readonly_query(
                self.schema_manager, pod_id, query, principal
            )
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

        list_sql += " ORDER BY " + order_by_clause(ctx, sorts)

        list_sql += " LIMIT :limit OFFSET :offset"
        params["limit"] = limit + 1  # +1: `rows_and_total` skips the COUNT
        params["offset"] = offset

        try:
            async with self.schema_manager.session_factory() as session:
                principal = ctx.row_principal(
                    user_id, is_pod_admin=not enforce_user_scope
                )
                if principal is not None:
                    await self.schema_manager.set_rls_context(session, principal)

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
        changes: ChangeCapture,
        enforce_user_scope: bool = True,
        expected_updated_at: datetime | None = None,
    ) -> RecordEntity:
        return await logged.update_logged(
            self,
            ctx,
            record_id,
            data,
            user_id,
            changes,
            enforce_user_scope=enforce_user_scope,
            expected_updated_at=expected_updated_at,
        )

    async def delete_record(
        self,
        ctx: TableContext,
        record_id: Any,
        user_id: UUID,
        *,
        changes: ChangeCapture,
        enforce_user_scope: bool = True,
    ) -> RecordEntity:
        return await logged.delete_logged(
            self,
            ctx,
            record_id,
            user_id,
            changes,
            enforce_user_scope=enforce_user_scope,
        )
