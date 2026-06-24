from typing import List
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    validate_computed_expression,
)
from app.modules.datastore.domain.errors import (
    DatastoreValidationError,
)
from app.modules.datastore.infrastructure.session import (
    close_datastore_engine,
    get_datastore_engine,
    get_datastore_session_maker,
)
from app.modules.datastore.infrastructure.sql_identifiers import (
    map_datastore_db_error,
    quote_sql_literal,
    sanitize_identifier,
)
from app.modules.datastore.config import datastore_settings
from app.core.log.log import get_logger

logger = get_logger(__name__)


class SchemaManager:
    """Manages physical database operations for datastores with RLS support."""

    def __init__(self, engine=None, session_factory=None):
        self._owns_engine = engine is not None
        self._engine = engine or get_datastore_engine()
        self.session_factory = session_factory or get_datastore_session_maker()
        self._query_role_ready = False

    def _query_role_identifier(self) -> str:
        """Validated identifier for the RLS-subject role used by ad-hoc queries."""
        return self._sanitize_identifier(datastore_settings.datastore_query_role)

    async def ensure_query_role(self) -> None:
        """Idempotently create the read-only, RLS-subject query role.

        Ad-hoc SQL (``query.execute``) runs under this role via ``SET LOCAL ROLE``
        so row-level security is actually enforced — the application's own
        connection is a superuser/BYPASSRLS role that would otherwise see every
        row. The role is ``NOLOGIN`` (entered only via ``SET ROLE``) and granted
        to the connecting role so a non-superuser app role can switch into it.
        """
        if self._query_role_ready:
            return
        role = self._query_role_identifier()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    f'DO $$ BEGIN CREATE ROLE "{role}" '
                    "NOLOGIN NOSUPERUSER NOBYPASSRLS; "
                    "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
                )
            )
            await conn.execute(text(f'GRANT "{role}" TO CURRENT_USER'))
        self._query_role_ready = True

    async def _grant_query_role_on_table(
        self, conn, schema_name: str, table_name: str
    ) -> None:
        """Grant the query role read access to one table (and its schema)."""
        role = self._query_role_identifier()
        await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{role}"'))
        await conn.execute(
            text(f'GRANT SELECT ON "{schema_name}"."{table_name}" TO "{role}"')
        )

    async def _try_grant_query_role(self, schema_name: str, table_name: str) -> None:
        """Best-effort grant of read access to the query role; never raises.

        Runs in its own transaction so a failure (e.g. the app role lacks
        CREATEROLE/GRANT) cannot roll back table creation. Missing grants surface
        as fail-closed query errors, repairable via ``backfill_query_role_grants``.
        """
        try:
            await self.ensure_query_role()
            async with self._engine.begin() as conn:
                await self._grant_query_role_on_table(conn, schema_name, table_name)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not grant query role read on %s.%s; ad-hoc queries on it "
                "will fail until grants are repaired",
                schema_name,
                table_name,
                exc_info=True,
            )

    async def backfill_query_role_grants(self) -> None:
        """Grant the query role read access across all existing pod schemas.

        Idempotent; covers pods whose schemas/tables were created before the role
        mechanism existed. Safe to run at every startup.
        """
        await self.ensure_query_role()
        role = self._query_role_identifier()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "DO $$ DECLARE s text; BEGIN "
                    "FOR s IN SELECT nspname FROM pg_namespace "
                    "WHERE nspname LIKE 'pod\\_%' LOOP "
                    f"EXECUTE format('GRANT USAGE ON SCHEMA %I TO \"{role}\"', s); "
                    "EXECUTE format("
                    f"'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO \"{role}\"', s); "
                    "END LOOP; END $$"
                )
            )

    def _get_schema_name(self, pod_id: UUID) -> str:
        return f"pod_{str(pod_id).replace('-', '_')}"

    def get_schema_name(self, pod_id: UUID) -> str:
        return self._get_schema_name(pod_id)

    def _sanitize_identifier(self, identifier: str) -> str:
        return sanitize_identifier(identifier)

    def _get_postgres_type(self, column: ColumnSchema) -> str:
        if column.computed:
            return None  # Should not be called for computed

        type_mapping = {
            DatastoreDataType.TEXT: "TEXT"
            if not column.max_length
            else f"VARCHAR({column.max_length})",
            DatastoreDataType.FILE_PATH: "TEXT"
            if not column.max_length
            else f"VARCHAR({column.max_length})",
            DatastoreDataType.INTEGER: "INTEGER",
            DatastoreDataType.FLOAT: "NUMERIC",
            DatastoreDataType.BOOLEAN: "BOOLEAN",
            DatastoreDataType.DATE: "DATE",
            DatastoreDataType.DATETIME: "TIMESTAMP WITH TIME ZONE",
            DatastoreDataType.JSON: "JSONB",
            DatastoreDataType.UUID: "UUID",
            DatastoreDataType.USER: "UUID",
            DatastoreDataType.SERIAL: "SERIAL",
            DatastoreDataType.ENUM: "TEXT",
            DatastoreDataType.VECTOR: "TEXT",
        }
        pg_type = type_mapping.get(column.type)
        if pg_type is None:
            raise DatastoreValidationError(f"Unsupported column type: {column.type}")
        return pg_type

    def _get_postgres_type_for_computed(self, column: ColumnSchema) -> str:
        type_mapping = {
            DatastoreDataType.TEXT: "TEXT",
            DatastoreDataType.FILE_PATH: "TEXT",
            DatastoreDataType.INTEGER: "INTEGER",
            DatastoreDataType.FLOAT: "NUMERIC",
            DatastoreDataType.BOOLEAN: "BOOLEAN",
            DatastoreDataType.DATE: "DATE",
            DatastoreDataType.DATETIME: "TIMESTAMP WITH TIME ZONE",
            DatastoreDataType.JSON: "JSONB",
            DatastoreDataType.UUID: "UUID",
            DatastoreDataType.USER: "UUID",
        }
        return type_mapping.get(column.type, "TEXT")

    def _enum_check_clause(self, column: ColumnSchema) -> str:
        """Build a CHECK constraint enforcing ENUM options at the DB level."""
        if column.type != DatastoreDataType.ENUM or not column.options:
            return ""
        options_sql = ", ".join(quote_sql_literal(option) for option in column.options)
        return f' CHECK ("{column.name}" IN ({options_sql}))'

    def _build_foreign_key_clause(self, schema_name: str, column: ColumnSchema) -> str:
        if column.foreign_key is None:
            return ""

        reference = column.foreign_key.references.strip()
        parts = reference.split(".")
        if len(parts) != 2:
            raise DatastoreValidationError(
                "Foreign key references must use the format 'table.column'"
            )

        referenced_table, referenced_column = parts
        self._sanitize_identifier(referenced_table)
        self._sanitize_identifier(referenced_column)
        return (
            f' REFERENCES "{schema_name}"."{referenced_table}"'
            f'("{referenced_column}")'
        )

    def _map_db_error(
        self,
        *,
        operation: str,
        exc: Exception,
        table_name: str | None = None,
        column_name: str | None = None,
        columns: list | None = None,
    ) -> Exception:
        return map_datastore_db_error(
            operation=operation,
            exc=exc,
            table_name=table_name,
            column_name=column_name,
            columns=columns,
        )

    async def create_datastore_schema(self, pod_id: UUID) -> None:
        schema_name = self._get_schema_name(pod_id)
        async with self._engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
        logger.info(f"Created schema {schema_name} for pod {pod_id}")

    async def drop_datastore_schema(self, pod_id: UUID) -> None:
        schema_name = self._get_schema_name(pod_id)
        async with self._engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        logger.info(f"Dropped schema {schema_name} for pod {pod_id}")

    async def create_table(
        self,
        pod_id: UUID,
        table_name: str,
        primary_key_column: str,
        columns: List[ColumnSchema],
        enable_rls: bool = True,
    ) -> None:
        schema_name = self._get_schema_name(pod_id)
        self._sanitize_identifier(table_name)
        self._sanitize_identifier(primary_key_column)

        column_defs = []
        pk_col = next((c for c in columns if c.name == primary_key_column), None)

        if pk_col:
            self._sanitize_identifier(pk_col.name)
            col_type = (
                "SERIAL"
                if (pk_col.type == DatastoreDataType.INTEGER and pk_col.auto)
                else self._get_postgres_type(pk_col)
            )

            col_def = f'"{pk_col.name}" {col_type} PRIMARY KEY'
            if pk_col.type in {DatastoreDataType.UUID, DatastoreDataType.USER}:
                col_def += " DEFAULT gen_random_uuid()"
            col_def += self._build_foreign_key_clause(schema_name, pk_col)
            column_defs.append(col_def)
        elif primary_key_column == "id":
            column_defs.append(
                f'"{primary_key_column}" UUID PRIMARY KEY DEFAULT gen_random_uuid()'
            )
        else:
            raise DatastoreValidationError(
                f"Primary key column '{primary_key_column}' not found"
            )

        auto_columns = ["created_at", "updated_at"]
        if enable_rls:
            auto_columns.append("user_id")

        added_columns = {primary_key_column}
        for auto_col in auto_columns:
            if auto_col in added_columns:
                continue
            if auto_col in ["created_at", "updated_at"]:
                column_defs.append(
                    f'"{auto_col}" TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP'
                )
            else:
                column_defs.append(f'"{auto_col}" UUID')
            added_columns.add(auto_col)

        computed_columns = []
        for col in columns:
            if col.name == primary_key_column or col.name in auto_columns:
                continue
            if col.computed:
                computed_columns.append(col)
                continue

            self._sanitize_identifier(col.name)
            col_type = self._get_postgres_type(col)
            col_def = f'"{col.name}" {col_type}'

            if col.auto and col.type == DatastoreDataType.INTEGER:
                col_def = f'"{col.name}" SERIAL'
            elif col.auto and col.type in {
                DatastoreDataType.UUID,
                DatastoreDataType.USER,
            }:
                col_def = f'"{col.name}" UUID NOT NULL DEFAULT gen_random_uuid()'

            if not col.required and not col.auto:
                col_def += " NULL"
            elif not col.auto:
                col_def += " NOT NULL"

            if col.default is not None and not col.auto:
                col_def += f" DEFAULT {quote_sql_literal(col.default)}"

            if col.unique:
                col_def += " UNIQUE"
            col_def += self._enum_check_clause(col)
            col_def += self._build_foreign_key_clause(schema_name, col)

            column_defs.append(col_def)

        create_table_sql = (
            f'CREATE TABLE "{schema_name}"."{table_name}" ({", ".join(column_defs)})'
        )

        try:
            async with self._engine.begin() as conn:
                # Serialize schema bootstrap for a pod to avoid rare concurrent
                # CREATE SCHEMA races in PostgreSQL (seen as pg_namespace unique violations).
                await conn.execute(
                    text(
                        "SELECT pg_advisory_xact_lock(hashtext(:schema_name))"
                    ),
                    {"schema_name": schema_name},
                )
                await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
                await conn.execute(text(create_table_sql))

                if enable_rls:
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" ENABLE ROW LEVEL SECURITY'
                        )
                    )
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" FORCE ROW LEVEL SECURITY'
                        )
                    )
                    await conn.execute(
                        text(self._user_isolation_policy_sql(schema_name, table_name))
                    )

                known_column_names = {c.name for c in columns}
                known_column_names.update(added_columns)
                for col in computed_columns:
                    self._sanitize_identifier(col.name)
                    validate_computed_expression(col.expression, known_column_names)
                    col_type = self._get_postgres_type_for_computed(col)
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" ADD COLUMN "{col.name}" {col_type} GENERATED ALWAYS AS ({col.expression}) STORED'
                        )
                    )
        except DBAPIError as exc:
            raise self._map_db_error(
                operation=f"create table '{table_name}'",
                exc=exc,
                table_name=table_name,
            ) from exc

        # Best-effort, outside the table-creation transaction: let ad-hoc queries
        # (run under the RLS-subject role) read this table. Never block table
        # creation on role/grant issues — queries fail closed and the startup
        # backfill or a retry repairs missing grants.
        await self._try_grant_query_role(schema_name, table_name)

    async def drop_table(self, pod_id: UUID, table_name: str) -> None:
        schema_name = self._get_schema_name(pod_id)
        self._sanitize_identifier(table_name)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(f'DROP TABLE IF EXISTS "{schema_name}"."{table_name}" CASCADE')
                )
        except DBAPIError as exc:
            raise self._map_db_error(
                operation=f"drop table '{table_name}'",
                exc=exc,
                table_name=table_name,
            ) from exc

    async def add_column(
        self,
        pod_id: UUID,
        table_name: str,
        column: ColumnSchema,
        known_columns: set[str] | None = None,
    ) -> None:
        schema_name = self._get_schema_name(pod_id)
        self._sanitize_identifier(table_name)
        self._sanitize_identifier(column.name)

        if column.computed:
            # The new column may reference its siblings; include itself too.
            allowed = set(known_columns or set())
            allowed.add(column.name)
            validate_computed_expression(column.expression, allowed)
            col_type = self._get_postgres_type_for_computed(column)
            col_def = f'"{column.name}" {col_type} GENERATED ALWAYS AS ({column.expression}) STORED'
        else:
            if column.auto and column.type == DatastoreDataType.INTEGER:
                col_def = f'"{column.name}" SERIAL'
            elif column.auto and column.type in {
                DatastoreDataType.UUID,
                DatastoreDataType.USER,
            }:
                col_def = f'"{column.name}" UUID NOT NULL DEFAULT gen_random_uuid()'
            else:
                col_type = self._get_postgres_type(column)
                col_def = f'"{column.name}" {col_type}'
                col_def += " NOT NULL" if column.required else " NULL"
                if column.default is not None:
                    col_def += f" DEFAULT {quote_sql_literal(column.default)}"
            if column.unique:
                col_def += " UNIQUE"
            col_def += self._enum_check_clause(column)
            col_def += self._build_foreign_key_clause(schema_name, column)

        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(f'ALTER TABLE "{schema_name}"."{table_name}" ADD COLUMN {col_def}')
                )
        except DBAPIError as exc:
            raise self._map_db_error(
                operation=f"add column '{column.name}'",
                exc=exc,
                table_name=table_name,
                column_name=column.name,
                columns=[column],
            ) from exc

    async def drop_column(
        self, pod_id: UUID, table_name: str, column_name: str
    ) -> None:
        schema_name = self._get_schema_name(pod_id)
        self._sanitize_identifier(table_name)
        self._sanitize_identifier(column_name)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        f'ALTER TABLE "{schema_name}"."{table_name}" DROP COLUMN "{column_name}"'
                    )
                )
        except DBAPIError as exc:
            raise self._map_db_error(
                operation=f"drop column '{column_name}'",
                exc=exc,
                table_name=table_name,
                column_name=column_name,
            ) from exc

    def _user_isolation_policy_sql(self, schema_name: str, table_name: str) -> str:
        """The per-user row-isolation policy shared by table create and the RLS
        toggle: a row is visible/writable to pod admins or to its ``user_id``
        owner, both read off the session-local RLS context."""
        policy_name = f"{table_name}_user_isolation"
        return (
            f'CREATE POLICY "{policy_name}" ON "{schema_name}"."{table_name}" '
            "USING ("
            "NULLIF(current_setting('app.current_user_is_pod_admin', TRUE), '')::BOOLEAN "
            "OR user_id = NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID"
            ") "
            "WITH CHECK ("
            "NULLIF(current_setting('app.current_user_is_pod_admin', TRUE), '')::BOOLEAN "
            "OR user_id = NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID"
            ")"
        )

    async def set_table_rls(
        self,
        pod_id: UUID,
        table_name: str,
        enable: bool,
    ) -> None:
        """Enable or disable per-user row-level security on an existing table.

        Only permitted on an empty table: enabling RLS makes every row require a
        ``user_id`` owner, so existing rows would have ambiguous ownership.
        Enabling materializes the ``user_id`` column (if absent) and installs the
        isolation policy; disabling drops the policy and turns RLS off, leaving
        the now-unused ``user_id`` column in place.
        """
        schema_name = self._get_schema_name(pod_id)
        self._sanitize_identifier(table_name)
        policy_name = f"{table_name}_user_isolation"
        try:
            async with self._engine.begin() as conn:
                count = await conn.execute(
                    text(f'SELECT COUNT(*) FROM "{schema_name}"."{table_name}"')
                )
                if (count.scalar() or 0) > 0:
                    raise DatastoreValidationError(
                        "Row-level security can only be toggled on an empty table. "
                        "Export and clear the table's rows first."
                    )
                if enable:
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" '
                            'ADD COLUMN IF NOT EXISTS "user_id" UUID'
                        )
                    )
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" ENABLE ROW LEVEL SECURITY'
                        )
                    )
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" FORCE ROW LEVEL SECURITY'
                        )
                    )
                    await conn.execute(
                        text(
                            f'DROP POLICY IF EXISTS "{policy_name}" '
                            f'ON "{schema_name}"."{table_name}"'
                        )
                    )
                    await conn.execute(
                        text(self._user_isolation_policy_sql(schema_name, table_name))
                    )
                else:
                    await conn.execute(
                        text(
                            f'DROP POLICY IF EXISTS "{policy_name}" '
                            f'ON "{schema_name}"."{table_name}"'
                        )
                    )
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" NO FORCE ROW LEVEL SECURITY'
                        )
                    )
                    await conn.execute(
                        text(
                            f'ALTER TABLE "{schema_name}"."{table_name}" DISABLE ROW LEVEL SECURITY'
                        )
                    )
        except DBAPIError as exc:
            raise self._map_db_error(
                operation=f"toggle RLS on table '{table_name}'",
                exc=exc,
                table_name=table_name,
            ) from exc

    async def set_rls_context(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        is_pod_admin: bool = False,
    ) -> None:
        """Sets the RLS context for the current session."""
        await session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )
        await session.execute(
            text(
                "SELECT set_config("
                "'app.current_user_is_pod_admin', "
                ":is_pod_admin, "
                "true"
                ")"
            ),
            {"is_pod_admin": "true" if is_pod_admin else "false"},
        )

    async def close(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()
            return
        await close_datastore_engine()
