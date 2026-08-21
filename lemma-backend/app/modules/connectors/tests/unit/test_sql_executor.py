"""Unit tests for the SQL connector executor (safety + op dispatch, no live DB)."""

from __future__ import annotations

import pytest

from app.modules.connectors.domain.errors import OperationExecutionValidationError
from app.modules.connectors.infrastructure.adapters.sql_executor import (
    SqlExecutor,
    _ensure_read_only,
)

CONN = {"dialect": "postgresql", "host": "localhost", "port": 5432, "database": "db"}
CREDS = {"username": "u", "password": "p"}


def test_ensure_read_only_allows_selects():
    _ensure_read_only("SELECT * FROM users")
    _ensure_read_only("WITH t AS (SELECT 1 AS x) SELECT x FROM t")
    _ensure_read_only("SELECT a FROM x UNION SELECT b FROM y")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO users (id) VALUES (1)",
        "UPDATE users SET name = 'x'",
        "DELETE FROM users",
        "DROP TABLE users",
        "CREATE TABLE t (id int)",
        "TRUNCATE users",
        "SELECT 1; DROP TABLE users",  # stacked
        "WITH d AS (DELETE FROM users RETURNING *) SELECT * FROM d",  # mutation in CTE
    ],
)
def test_ensure_read_only_rejects_mutations(sql):
    with pytest.raises(OperationExecutionValidationError):
        _ensure_read_only(sql)


async def _run(op, payload, connection_config=CONN):
    return await SqlExecutor().execute(
        connector_id="sql",
        operation_name=op,
        execution={"kind": "sql", "op": op},
        payload=payload,
        third_party_credentials=CREDS,
        connection_config=connection_config,
    )


# OperationExecutionValidationError carries a fixed, non-reflecting message by
# design (see domain/errors.py), so these assert on the type and on the fact that
# rejection happens with no reachable database -- never on the message text.


@pytest.mark.asyncio
async def test_query_requires_non_empty_query():
    with pytest.raises(OperationExecutionValidationError):
        await _run("query", {"query": "   "})


@pytest.mark.asyncio
async def test_query_read_only_enforced_before_connect():
    # A write query is rejected by validation before any DB connection is opened.
    with pytest.raises(OperationExecutionValidationError):
        await _run("query", {"query": "DELETE FROM users"})


@pytest.mark.asyncio
async def test_describe_table_requires_table():
    with pytest.raises(OperationExecutionValidationError):
        await _run("describe_table", {})


@pytest.mark.asyncio
async def test_unsupported_op_rejected():
    with pytest.raises(OperationExecutionValidationError):
        await _run("drop_everything", {})


@pytest.mark.asyncio
async def test_missing_host_or_database_rejected():
    with pytest.raises(OperationExecutionValidationError):
        await _run(
            "query", {"query": "SELECT 1"}, connection_config={"dialect": "postgresql"}
        )


@pytest.mark.asyncio
async def test_unsupported_dialect_rejected():
    with pytest.raises(OperationExecutionValidationError):
        await _run(
            "query",
            {"query": "SELECT 1"},
            connection_config={"dialect": "oracle", "host": "h", "database": "d"},
        )


class TestEnginePooling:
    """The pool is keyed on connection identity, never on the password."""

    @pytest.mark.asyncio
    async def test_same_connection_reuses_one_engine(self):
        executor = SqlExecutor()
        first = await executor._engine_for(CONN, CREDS)
        second = await executor._engine_for(CONN, CREDS)
        assert first is second
        await executor.dispose_all()

    @pytest.mark.asyncio
    async def test_a_rotated_password_does_not_reuse_the_old_pool(self):
        # The password is not part of the cache key, so nothing about the key
        # distinguishes these two calls. Without the constant-time comparison
        # the tenant would keep querying on connections opened with the
        # credential they just revoked.
        executor = SqlExecutor()
        before = await executor._engine_for(CONN, CREDS)
        after = await executor._engine_for(
            CONN, {"username": "u", "password": "rotated"}
        )
        assert after is not before
        assert len(executor._engines) == 1
        await executor.dispose_all()

    @pytest.mark.asyncio
    async def test_no_cache_key_contains_the_password(self):
        executor = SqlExecutor()
        await executor._engine_for(CONN, {"username": "u", "password": "hunter2"})
        assert all("hunter2" not in key for key in executor._engines)
        await executor.dispose_all()

    @pytest.mark.asyncio
    async def test_distinct_databases_get_distinct_engines(self):
        executor = SqlExecutor()
        first = await executor._engine_for(CONN, CREDS)
        second = await executor._engine_for({**CONN, "database": "other"}, CREDS)
        assert first is not second
        assert len(executor._engines) == 2
        await executor.dispose_all()
