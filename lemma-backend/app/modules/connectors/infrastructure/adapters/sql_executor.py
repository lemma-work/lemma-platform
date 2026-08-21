"""SQL connector executor — read-only queries against an external database.

An ``sql``-kind connector stores its connection config on the auth-config
(``connection_config``: dialect/host/port/database) and per-user secrets on the
account (``third_party_credentials``: username/password). Operations are a fixed
set — ``query`` / ``list_tables`` / ``describe_table`` — selected by the
``execution`` descriptor's ``op``.

Safety: ``query`` accepts only a single read-only SELECT-family statement
(validated with sqlglot, reusing the datastore's forbidden-node/allowed-root
rules), runs in a ``READ ONLY`` transaction with a ``statement_timeout``, and
caps returned rows. Engines are cached per connection and reused across calls.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict
from typing import Any
from urllib.parse import quote_plus

import sqlglot
from asyncpg.exceptions import PostgresError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionValidationError,
)

logger = get_logger(__name__)

# Reuse the datastore read-only policy (mutation/DDL nodes forbidden anywhere).
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Copy,
    exp.Command,
)
_ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
    exp.Subquery,
    exp.With,
)

_DIALECT_DRIVERS = {
    "postgresql": "postgresql+asyncpg",
    "postgres": "postgresql+asyncpg",
}
_DEFAULT_ROW_CAP = 1000
_MAX_ROW_CAP = 10_000
_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
# Parsing is CPU on the event loop and the query is tenant-supplied, so its
# length has to be bounded by something other than the caller's goodwill.
# Generous against any hand-written or generated analytical query; a megabyte
# of SQL is a payload, not a question.
_MAX_SQL_CHARS = 256 * 1024


def _ensure_read_only(sql: str) -> None:
    if len(sql) > _MAX_SQL_CHARS:
        raise OperationExecutionValidationError(
            f"SQL query exceeds the {_MAX_SQL_CHARS} character limit.",
            details={"reason": "query_too_long"},
        )
    try:
        statements = [
            s for s in sqlglot.parse(sql, dialect="postgres") if s is not None
        ]
    except SqlglotError as exc:
        raise OperationExecutionValidationError(
            f"Could not parse SQL query: {exc}"
        ) from exc
    if not statements:
        raise OperationExecutionValidationError("Empty SQL query.")
    if len(statements) > 1:
        raise OperationExecutionValidationError(
            "Only a single SQL statement is allowed."
        )
    statement = statements[0]
    if not isinstance(statement, _ALLOWED_ROOTS) or statement.find(*_FORBIDDEN_NODES):
        raise OperationExecutionValidationError(
            "Only read-only SELECT queries are allowed."
        )


def _resolve_row_cap(connection_config: dict[str, Any]) -> int:
    """Clamp the caller-supplied row cap.

    ``row_cap`` comes from the install config, which is tenant-written. Every row
    is materialized into a dict before returning, so an unbounded value is a
    straightforward way to exhaust the worker's memory; a non-numeric one used to
    raise ``ValueError`` out of ``int()`` as an unhandled 500.
    """
    raw = connection_config.get("row_cap")
    if raw is None:
        return _DEFAULT_ROW_CAP
    try:
        requested = int(raw)
    except TypeError, ValueError:
        raise OperationExecutionValidationError(
            "SQL connection 'row_cap' must be an integer.",
            details={"reason": "row_cap_not_an_integer"},
        ) from None
    if requested <= 0:
        return _DEFAULT_ROW_CAP
    return min(requested, _MAX_ROW_CAP)


class SqlExecutor:
    """Runs read-only queries against a tenant's own database.

    Engines are pooled per connection because building one per call would defeat
    connection pooling entirely. The key covers only the non-secret half of the
    connection -- driver, user, host, port, database -- so no password is ever
    hashed or used as a dict key.

    A rotated password still has to invalidate the pooled engine, or the tenant
    would keep reaching their database with a credential they revoked. That is
    handled by comparing the secret against the cached entry in constant time
    rather than by folding it into the key. The comparison holds the password in
    the entry, which costs nothing: SQLAlchemy already retains it inside the
    engine's URL so the pool can reconnect.
    """

    def __init__(self) -> None:
        self._engines: "OrderedDict[str, tuple[AsyncEngine, bytes]]" = OrderedDict()

    async def execute(
        self,
        *,
        connector_id: str,
        operation_name: str,
        execution: dict[str, Any],
        payload: dict[str, Any],
        third_party_credentials: dict[str, Any] | None,
        connection_config: dict[str, Any] | None = None,
    ) -> Any:
        op = (execution or {}).get("op") or ""
        builder = self._STATEMENTS.get(op)
        if builder is None:
            raise OperationExecutionValidationError(
                f"Unsupported SQL operation '{op}' for '{operation_name}'.",
                details={"reason": "unsupported_operation"},
            )
        sql, params = builder(payload or {})
        engine = await self._engine_for(
            connection_config or {}, third_party_credentials or {}
        )
        return await self._run_select(
            engine,
            sql,
            row_cap=_resolve_row_cap(connection_config or {}),
            params=params,
        )

    # --- statement builders --------------------------------------------------
    # Each returns (sql, params) and validates its own inputs, so `execute` is a
    # lookup rather than a chain of branches.

    @staticmethod
    def _build_query(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        sql = str(payload.get("query") or "").strip()
        if not sql:
            raise OperationExecutionValidationError(
                "A 'query' string is required.", details={"reason": "empty_query"}
            )
        _ensure_read_only(sql)
        return sql, None

    @staticmethod
    def _build_list_tables(
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        schema = payload.get("schema")
        sql = (
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            + ("AND table_schema = :schema " if schema else "")
            + "ORDER BY table_schema, table_name"
        )
        return sql, ({"schema": schema} if schema else None)

    @staticmethod
    def _build_describe_table(
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        table = payload.get("table")
        if not table:
            raise OperationExecutionValidationError(
                "A 'table' name is required.", details={"reason": "missing_table"}
            )
        schema = payload.get("schema")
        # Without a schema this describes every same-named table across schemas;
        # the schema column in the projection is what disambiguates them.
        sql = (
            "SELECT table_schema, column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = :table "
            + ("AND table_schema = :schema " if schema else "")
            + "ORDER BY table_schema, ordinal_position"
        )
        params = {"table": table} | ({"schema": schema} if schema else {})
        return sql, params

    _STATEMENTS = {
        "query": _build_query,
        "list_tables": _build_list_tables,
        "describe_table": _build_describe_table,
    }

    # --- connection ---------------------------------------------------------

    async def _engine_for(
        self, connection_config: dict[str, Any], creds: dict[str, Any]
    ) -> AsyncEngine:
        dialect = str(connection_config.get("dialect") or "postgresql").lower()
        driver = _DIALECT_DRIVERS.get(dialect)
        if driver is None:
            raise OperationExecutionValidationError(
                f"Unsupported SQL dialect '{dialect}'. Supported: postgresql.",
                details={"reason": "unsupported_dialect"},
            )
        host = connection_config.get("host")
        database = connection_config.get("database")
        if not host or not database:
            raise OperationExecutionValidationError(
                "SQL connection requires 'host' and 'database'.",
                details={"reason": "missing_host_or_database"},
            )
        port = connection_config.get("port") or 5432
        user = quote_plus(str(creds.get("username") or ""))
        password = quote_plus(str(creds.get("password") or ""))
        userinfo = f"{user}:{password}@" if user else ""
        dsn = f"{driver}://{userinfo}{host}:{port}/{database}"
        # The key identifies the connection; it deliberately excludes the secret.
        cache_key = hashlib.sha256(
            f"{driver}\0{user}\0{host}\0{port}\0{database}".encode()
        ).hexdigest()
        secret = password.encode()

        cached = self._engines.get(cache_key)
        if cached is not None:
            engine, cached_secret = cached
            if hmac.compare_digest(cached_secret, secret):
                self._engines.move_to_end(cache_key)
                return engine
            # The password changed under the same connection identity. Reusing
            # the pool here would keep serving queries on connections opened
            # with the revoked credential.
            del self._engines[cache_key]
            await engine.dispose()

        engine = create_async_engine(
            dsn, pool_size=2, max_overflow=2, pool_pre_ping=True
        )
        self._engines[cache_key] = (engine, secret)
        self._engines.move_to_end(cache_key)
        while len(self._engines) > connector_settings.connector_sql_engine_cache_size:
            _evicted_key, (evicted, _secret) = self._engines.popitem(last=False)
            # Dropping the reference is not enough: the engine owns a live pool
            # against a customer database, and garbage collection will not close
            # those sockets promptly (or at all, for asyncpg). Without this the
            # process leaks connections to every database past the cache size.
            await evicted.dispose()
        return engine

    async def dispose_all(self) -> None:
        """Close every pooled engine. Called on process shutdown."""
        engines = [engine for engine, _secret in self._engines.values()]
        self._engines.clear()
        for engine in engines:
            await engine.dispose()

    async def _run_select(
        self,
        engine: AsyncEngine,
        sql: str,
        *,
        row_cap: int,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(
                    text(f"SET statement_timeout = {_DEFAULT_STATEMENT_TIMEOUT_MS}")
                )
                if params is None:
                    # The tenant's own SQL goes to the driver verbatim.
                    # `text()` would scan it for ``:name`` bind parameters, so a
                    # legitimate query containing a colon -- a jsonb literal like
                    # '{"a":1}', a cast, a time string -- would be rejected as a
                    # missing bind rather than being run.
                    result = await conn.exec_driver_sql(sql)
                else:
                    # Our own introspection statements, which do use binds.
                    result = await conn.execute(text(sql), params)
                columns = list(result.keys())
                rows = result.fetchmany(row_cap + 1)
        except OperationExecutionValidationError:
            raise
        except (SQLAlchemyError, OSError, PostgresError) as exc:
            raise OperationExecutionInfrastructureError(
                f"SQL execution failed: {exc}",
                details={"provider": "sql", "upstream_message": str(exc)},
            ) from exc

        truncated = len(rows) > row_cap
        rows = rows[:row_cap]
        return {
            "columns": columns,
            "rows": [dict(zip(columns, _coerce_row(row))) for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
        }


def _coerce_row(row: Any) -> list[Any]:
    values = []
    for v in row:
        if isinstance(v, (str, int, float, bool)) or v is None:
            values.append(v)
        else:
            values.append(str(v))
    return values


# Engines hold pools, so the executor is process-shared rather than per-request:
# a fresh one per call would open and abandon a pool every time.
_SHARED_SQL_EXECUTOR: SqlExecutor | None = None


def shared_sql_executor() -> SqlExecutor:
    global _SHARED_SQL_EXECUTOR
    if _SHARED_SQL_EXECUTOR is None:
        _SHARED_SQL_EXECUTOR = SqlExecutor()
    return _SHARED_SQL_EXECUTOR


async def dispose_shared_sql_engines() -> None:
    """Close every pooled external engine. Called from the app lifespan."""
    global _SHARED_SQL_EXECUTOR
    if _SHARED_SQL_EXECUTOR is not None:
        await _SHARED_SQL_EXECUTOR.dispose_all()
    _SHARED_SQL_EXECUTOR = None
