"""The SQL connector against a real PostgreSQL server.

The unit tests can only prove that sqlglot rejects a statement before we open a
connection. They cannot prove the thing that actually matters: that a write
which *slips past* the parser is still refused by the server, that
``statement_timeout`` really fires, or that an evicted engine really closes its
sockets. Those are properties of Postgres, not of our parsing, so they are
tested against Postgres.

The database under test is a second database on the same container the e2e stack
already runs, standing in for a customer's database. Nothing external is
involved, so this is hermetic and runs in CI alongside the other e2e tests.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionValidationError,
)
from app.modules.connectors.infrastructure.adapters.sql_executor import SqlExecutor
from app.modules.connectors.infrastructure.kinds import build_kind_registry
from app.modules.connectors.services.execution import KindDispatcher

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _tenant_db_name(worker_id: str) -> str:
    """Namespace the tenant database per xdist worker.

    The e2e stack now shares one Postgres server across all xdist workers
    (see ``shared_postgres`` in test_utils.py) rather than giving each
    worker its own container. A fixed name here would let two workers'
    concurrent DROP DATABASE/CREATE DATABASE calls race on the same
    database -- exactly what happened before this fix, surfacing as
    ``InvalidCatalogNameError: database "connector_sql_e2e" does not
    exist`` when one worker's teardown yanked it out from under another's
    still-running test.
    """
    import re

    return f"connector_sql_e2e_{re.sub(r'[^0-9a-zA-Z_]', '_', worker_id)}"


def _admin_url(test_database_url: str) -> str:
    return test_database_url.replace("postgresql+asyncpg", "postgresql+asyncpg", 1)


def _parts(url: str) -> dict:
    parsed = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "username": parsed.username,
        "password": parsed.password,
    }


@pytest_asyncio.fixture(scope="function")
async def tenant_database(test_database_url, worker_id):
    """A real database standing in for the customer's, seeded with real rows."""
    tenant_db = _tenant_db_name(worker_id)
    admin = create_async_engine(
        _admin_url(test_database_url), isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{tenant_db}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{tenant_db}"'))
    await admin.dispose()

    base = test_database_url.rsplit("/", 1)[0]
    tenant_url = f"{base}/{tenant_db}"
    engine = create_async_engine(tenant_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE invoices ("
                "  id integer PRIMARY KEY,"
                "  customer text NOT NULL,"
                "  amount_cents integer NOT NULL,"
                "  paid boolean NOT NULL DEFAULT false)"
            )
        )
        await conn.execute(text("CREATE SCHEMA reporting"))
        await conn.execute(
            text("CREATE TABLE reporting.invoices (id integer PRIMARY KEY)")
        )
        await conn.execute(
            text(
                "INSERT INTO invoices (id, customer, amount_cents, paid) "
                "SELECT g, 'customer-' || g, g * 100, mod(g, 2) = 0 "
                "FROM generate_series(1, 50) AS g"
            )
        )
    await engine.dispose()

    yield {"url": tenant_url, **_parts(test_database_url)}

    admin = create_async_engine(
        _admin_url(test_database_url), isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{tenant_db}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture
def connection_config(tenant_database, worker_id):
    return {
        "dialect": "postgresql",
        "host": tenant_database["host"],
        "port": tenant_database["port"],
        "database": _tenant_db_name(worker_id),
    }


@pytest.fixture
def credentials(tenant_database):
    return {
        "username": tenant_database["username"],
        "password": tenant_database["password"],
    }


async def _run(executor, op, payload, connection_config, credentials):
    return await executor.execute(
        connector_id="sql",
        operation_name=op,
        execution={"kind": "sql", "op": op},
        payload=payload,
        third_party_credentials=credentials,
        connection_config=connection_config,
    )


class TestReadPath:
    async def test_query_returns_real_rows_and_columns(
        self, connection_config, credentials
    ):
        result = await _run(
            SqlExecutor(),
            "query",
            {"query": "SELECT id, customer FROM invoices WHERE id <= 3 ORDER BY id"},
            connection_config,
            credentials,
        )
        assert result["columns"] == ["id", "customer"]
        assert result["rows"][0] == {"id": 1, "customer": "customer-1"}
        assert result["row_count"] == 3
        assert result["truncated"] is False

    async def test_list_tables_sees_both_schemas_and_hides_the_catalog(
        self, connection_config, credentials
    ):
        result = await _run(
            SqlExecutor(), "list_tables", {}, connection_config, credentials
        )
        found = {(row["table_schema"], row["table_name"]) for row in result["rows"]}
        assert ("public", "invoices") in found
        assert ("reporting", "invoices") in found
        assert not any(schema == "pg_catalog" for schema, _ in found)

    async def test_list_tables_can_be_scoped_to_one_schema(
        self, connection_config, credentials
    ):
        result = await _run(
            SqlExecutor(),
            "list_tables",
            {"schema": "reporting"},
            connection_config,
            credentials,
        )
        assert {row["table_schema"] for row in result["rows"]} == {"reporting"}

    async def test_describe_table_reports_real_column_types(
        self, connection_config, credentials
    ):
        result = await _run(
            SqlExecutor(),
            "describe_table",
            {"table": "invoices", "schema": "public"},
            connection_config,
            credentials,
        )
        columns = {row["column_name"]: row for row in result["rows"]}
        assert columns["amount_cents"]["data_type"] == "integer"
        assert columns["customer"]["is_nullable"] == "NO"
        assert columns["paid"]["column_default"] == "false"

    async def test_row_cap_truncates_and_says_so(self, connection_config, credentials):
        capped = {**connection_config, "row_cap": 10}
        result = await _run(
            SqlExecutor(),
            "query",
            {"query": "SELECT id FROM invoices ORDER BY id"},
            capped,
            credentials,
        )
        # 50 rows exist; the cap must both limit and flag, or a caller silently
        # believes it has the whole table.
        assert result["row_count"] == 10
        assert result["truncated"] is True

    async def test_non_scalar_values_survive_as_strings(
        self, connection_config, credentials
    ):
        result = await _run(
            SqlExecutor(),
            "query",
            {"query": "SELECT now()::timestamptz AS t, '{\"a\":1}'::jsonb AS j"},
            connection_config,
            credentials,
        )
        row = result["rows"][0]
        assert isinstance(row["t"], str) and isinstance(row["j"], str)


class TestTheServerEnforcesReadOnly:
    """sqlglot is a first gate, not the boundary. Postgres is the boundary."""

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO invoices (id, customer, amount_cents) VALUES (999, 'x', 1)",
            "UPDATE invoices SET paid = true",
            "DELETE FROM invoices",
            "DROP TABLE invoices",
            "CREATE TABLE sneaky (id int)",
        ],
    )
    async def test_writes_are_rejected(self, sql, connection_config, credentials):
        with pytest.raises(OperationExecutionValidationError):
            await _run(
                SqlExecutor(), "query", {"query": sql}, connection_config, credentials
            )

    async def test_a_write_smuggled_past_the_parser_still_fails_on_the_server(
        self, connection_config, credentials
    ):
        # A data-modifying CTE parses as a SELECT at the root. This is exactly
        # the case where the parser alone would not save us, so it proves the
        # READ ONLY transaction is doing real work.
        executor = SqlExecutor()
        engine = await executor._engine_for(connection_config, credentials)
        with pytest.raises(OperationExecutionInfrastructureError):
            await executor._run_select(
                engine,
                "WITH w AS (INSERT INTO invoices (id, customer, amount_cents) "
                "VALUES (998, 'smuggled', 1) RETURNING id) SELECT * FROM w",
                row_cap=10,
            )
        await executor.dispose_all()

    async def test_nothing_was_actually_written(self, connection_config, credentials):
        result = await _run(
            SqlExecutor(),
            "query",
            {"query": "SELECT count(*) AS n FROM invoices"},
            connection_config,
            credentials,
        )
        assert result["rows"][0]["n"] == 50


class TestFailureHandling:
    async def test_a_bad_query_becomes_a_domain_error_not_a_driver_traceback(
        self, connection_config, credentials
    ):
        with pytest.raises(OperationExecutionInfrastructureError):
            await _run(
                SqlExecutor(),
                "query",
                {"query": "SELECT * FROM table_that_does_not_exist"},
                connection_config,
                credentials,
            )

    async def test_wrong_password_surfaces_cleanly(self, connection_config):
        with pytest.raises(OperationExecutionInfrastructureError):
            await _run(
                SqlExecutor(),
                "query",
                {"query": "SELECT 1"},
                connection_config,
                {"username": "postgres", "password": "definitely-wrong"},
            )

    async def test_statement_timeout_stops_a_runaway_query(
        self, connection_config, credentials, monkeypatch
    ):
        # What is under test is that a statement_timeout is applied to the
        # connection at all -- a runaway tenant query has to be stopped by
        # Postgres rather than by hanging a worker. Which number it is set to
        # is configuration, and waiting out the real 30s default made this the
        # fifth-slowest test in the e2e suite for no extra proof. Shrink the
        # timeout and sleep past the small one instead.
        monkeypatch.setattr(
            "app.modules.connectors.infrastructure.adapters.sql_executor."
            "_DEFAULT_STATEMENT_TIMEOUT_MS",
            1_000,
        )
        executor = SqlExecutor()
        with pytest.raises(OperationExecutionInfrastructureError):
            await asyncio.wait_for(
                _run(
                    executor,
                    "query",
                    {"query": "SELECT pg_sleep(30)"},
                    connection_config,
                    credentials,
                ),
                # Comfortably longer than the patched timeout and far shorter
                # than the pg_sleep: if this deadline is what fires, the
                # statement_timeout did not.
                timeout=15,
            )
        await executor.dispose_all()


class TestEngineCache:
    async def test_the_same_connection_reuses_one_engine(
        self, connection_config, credentials
    ):
        executor = SqlExecutor()
        first = await executor._engine_for(connection_config, credentials)
        second = await executor._engine_for(connection_config, credentials)
        assert first is second
        await executor.dispose_all()

    async def test_eviction_disposes_the_engine_rather_than_leaking_it(
        self, connection_config, credentials, monkeypatch
    ):
        # The original dropped the reference and moved on, leaving a live pool
        # against the customer database for the life of the process.
        from app.modules.connectors.config import connector_settings

        monkeypatch.setattr(connector_settings, "connector_sql_engine_cache_size", 1)
        executor = SqlExecutor()
        first = await executor._engine_for(connection_config, credentials)
        # Open a real connection so the pool is genuinely holding a socket.
        async with first.connect() as conn:
            await conn.execute(text("SELECT 1"))
        assert first.pool.checkedin() == 1

        await executor._engine_for(
            {**connection_config, "database": "postgres"}, credentials
        )

        assert len(executor._engines) == 1
        # Disposal replaces the pool, so the connection it was holding is gone.
        assert first.pool.checkedin() == 0
        await executor.dispose_all()

    async def test_dispose_all_empties_the_cache(self, connection_config, credentials):
        executor = SqlExecutor()
        await executor._engine_for(connection_config, credentials)
        await executor.dispose_all()
        assert executor._engines == {}


class TestThroughTheDispatcher:
    """The same database, reached the way production reaches it."""

    async def test_sql_kind_executes_end_to_end(self, connection_config, credentials):
        from unittest.mock import AsyncMock

        dispatcher = KindDispatcher(
            build_kind_registry(
                composio_gateway=AsyncMock(), package_gateway=AsyncMock()
            )
        )
        request = dispatcher.build_request(
            connector_id="sql",
            kind=ConnectorKind.SQL,
            operation=ResolvedOperation(
                name="query", execution={"kind": "sql", "op": "query"}
            ),
            payload={"query": "SELECT count(*) AS n FROM invoices"},
            credentials=credentials,
            config=connection_config,
        )
        result = await dispatcher.execute(request)
        assert result["rows"][0]["n"] == 50
