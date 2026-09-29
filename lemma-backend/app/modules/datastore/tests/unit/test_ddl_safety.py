"""Unit tests for dynamic-table DDL safety (Workstream B).

Covers the computed-expression allow-list, SQL-literal quoting, ENUM CHECK
generation, and ColumnSchema construction-time rejection of injection payloads.
The full DDL-execution path is exercised end-to-end in the e2e suite.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sqlalchemy.exc import DBAPIError

from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreDataType,
    validate_computed_expression,
)
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.infrastructure.sql_identifiers import (
    quote_sql_literal,
    sanitize_identifier,
)

KNOWN = {"price", "qty", "name", "discount"}


class _CatalogResult:
    """Answers the query role's catalog probes as a provisioned deployment.

    ``ensure_role`` asks whether the role exists and whether the connecting
    role is a member before it issues DDL. A bare ``AsyncMock`` answers with a
    truthy coroutine, which happens to take the same branch but leaves the
    fake unable to describe any other deployment.
    """

    def scalar(self) -> bool:
        return True


@pytest.mark.parametrize(
    "expr",
    [
        "price * qty",
        "price * qty * (1 - discount)",
        "COALESCE(name, 'n/a')",
        "ROUND(price * qty, 2)",
        "UPPER(name) || '-' || LOWER(name)",
        "GREATEST(price, qty)",
        "price >= 10 AND qty < 5",
    ],
)
def test_valid_computed_expressions_accepted(expr: str) -> None:
    validate_computed_expression(expr, KNOWN)


@pytest.mark.parametrize(
    "expr",
    [
        "price) STORED; DROP TABLE projects; --",  # statement break
        "qty /* hidden */ + 1",  # block comment
        "name -- trailing",  # line comment
        "(SELECT current_user)",  # subquery
        "pg_sleep(1)",  # non-whitelisted function
        "price::regclass",  # cast
        "unknown_col + 1",  # unknown identifier
        "qty @ price",  # unsupported operator char
        "version()",  # non-whitelisted, no args
        "",  # empty
    ],
)
def test_malicious_or_invalid_expressions_rejected(expr: str) -> None:
    with pytest.raises(DatastoreValidationError):
        validate_computed_expression(expr, KNOWN)


def test_bare_identifiers_allowed_when_columns_unknown_but_calls_still_restricted() -> (
    None
):
    # construction-time pass (no known column set): column refs allowed...
    validate_computed_expression("price * qty")
    # ...but arbitrary function calls are still rejected even without columns.
    with pytest.raises(DatastoreValidationError):
        validate_computed_expression("pg_sleep(1)")


def test_quote_sql_literal_escapes_and_types() -> None:
    assert quote_sql_literal("a'b") == "'a''b'"
    assert quote_sql_literal("x'); DROP TABLE t; --") == "'x''); DROP TABLE t; --'"
    assert quote_sql_literal(True) == "TRUE"
    assert quote_sql_literal(False) == "FALSE"
    assert quote_sql_literal(5) == "5"
    assert quote_sql_literal(Decimal("1.50")) == "1.50"


@pytest.mark.parametrize(
    "bad", [object(), ["a"], {"k": "v"}, float("nan"), float("inf")]
)
def test_quote_sql_literal_rejects_unsupported(bad: object) -> None:
    with pytest.raises(DatastoreValidationError):
        quote_sql_literal(bad)


def test_sanitize_identifier_rejects_punctuation_and_empty() -> None:
    assert sanitize_identifier("valid_col1") == "valid_col1"
    for bad in ["", "drop table", "a;b", 'a"b', "a-b", "a.b"]:
        with pytest.raises(DatastoreValidationError):
            sanitize_identifier(bad)


def test_column_schema_rejects_injection_expression_at_construction() -> None:
    with pytest.raises(Exception):
        ColumnSchema(
            name="x",
            type=DatastoreDataType.TEXT,
            computed=True,
            expression="name) STORED; DROP TABLE t; --",
        )


def test_column_schema_rejects_non_scalar_default() -> None:
    with pytest.raises(Exception):
        ColumnSchema(name="x", type=DatastoreDataType.JSON, default={"k": "v"})


def test_column_schema_enum_default_must_be_in_options() -> None:
    with pytest.raises(Exception):
        ColumnSchema(
            name="s",
            type=DatastoreDataType.ENUM,
            options=["a", "b"],
            default="c",
        )
    # valid case constructs fine
    ColumnSchema(name="s", type=DatastoreDataType.ENUM, options=["a", "b"], default="a")


def test_enum_check_clause_quotes_options_safely() -> None:
    manager = SchemaManager.__new__(SchemaManager)  # no engine needed for this helper
    column = ColumnSchema(
        name="status",
        type=DatastoreDataType.ENUM,
        options=["open", "clo'sed"],
    )
    clause = manager._enum_check_clause(column)
    assert clause == """ CHECK ("status" IN ('open', 'clo''sed'))"""

    non_enum = ColumnSchema(name="t", type=DatastoreDataType.TEXT)
    assert manager._enum_check_clause(non_enum) == ""


def _provisioning_connection(*, schema_exists: bool = False) -> SimpleNamespace:
    """A connection on a provisioned deployment, and a catalog without the schema."""
    return SimpleNamespace(
        execute=AsyncMock(return_value=_CatalogResult()),
        scalar=AsyncMock(return_value=schema_exists),
    )


def _manager_on(connection: SimpleNamespace) -> SchemaManager:
    @asynccontextmanager
    async def begin():
        yield connection

    return SchemaManager(engine=SimpleNamespace(begin=begin), session_factory=object())


def _statements(connection: SimpleNamespace) -> list[str]:
    return [str(call.args[0]) for call in connection.execute.await_args_list]


@pytest.mark.asyncio
async def test_schema_provisioning_takes_shared_bootstrap_lock_before_create() -> None:
    connection = _provisioning_connection()
    manager = _manager_on(connection)
    pod_id = uuid4()

    await manager.create_datastore_schema(pod_id)

    statements = _statements(connection)
    lock = next(i for i, s in enumerate(statements) if "pg_advisory_xact_lock" in s)
    create = next(i for i, s in enumerate(statements) if "CREATE SCHEMA" in s)
    assert lock < create
    lock_call = connection.execute.await_args_list[lock]
    assert lock_call.args[1] == {"schema_name": manager.get_schema_name(pod_id)}


@pytest.mark.asyncio
async def test_a_new_schema_is_readable_by_the_query_role_from_birth() -> None:
    """Access rides the creating transaction, so no schema exists without it.

    The grant used to follow in a transaction of its own, best-effort, and a
    boot-time sweep over every pod schema repaired what it missed.
    """
    connection = _provisioning_connection()
    manager = _manager_on(connection)
    pod_id = uuid4()

    await manager.create_datastore_schema(pod_id)

    statements = _statements(connection)
    schema_name = manager.get_schema_name(pod_id)
    create = next(i for i, s in enumerate(statements) if "CREATE SCHEMA" in s)
    after_create = statements[create + 1 :]
    assert any(f'GRANT USAGE ON SCHEMA "{schema_name}"' in s for s in after_create)
    assert any(
        f'IN SCHEMA "{schema_name}" GRANT SELECT ON TABLES' in s for s in after_create
    )


@pytest.mark.asyncio
async def test_an_existing_schema_is_not_granted_again() -> None:
    """Every table creation passes through the bootstrap; only the first writes."""
    connection = _provisioning_connection(schema_exists=True)

    await _manager_on(connection).create_datastore_schema(uuid4())

    statements = _statements(connection)
    assert not any("CREATE SCHEMA" in s for s in statements)
    assert not any("GRANT USAGE ON SCHEMA" in s for s in statements)


@pytest.mark.asyncio
async def test_schema_provisioning_survives_a_role_that_cannot_be_created() -> None:
    """Grant failures must never block pod provisioning.

    On a deployment whose app role cannot create or grant roles, the schema
    still has to exist -- without the ACL, which it would fail to grant to a
    role that is not there. Queries fail closed until the role is provisioned.
    """
    calls: list[str] = []

    class _Missing:
        def scalar(self) -> None:
            return None

    class _Denied(DBAPIError):
        def __init__(self, statement: str):
            super().__init__(statement, {}, Exception("insufficient privilege"))

    async def execute(statement, *args):
        calls.append(str(statement))
        if "GRANT" in str(statement) or "CREATE ROLE" in str(statement):
            raise _Denied(str(statement))
        # Neither the role nor the membership exists, and neither can be
        # established: the deployment this test is named for.
        return _Missing()

    connection = SimpleNamespace(execute=execute, scalar=AsyncMock(return_value=False))

    await _manager_on(connection).create_datastore_schema(uuid4())

    assert any("CREATE SCHEMA IF NOT EXISTS" in statement for statement in calls)
    assert not any("GRANT USAGE ON SCHEMA" in statement for statement in calls)
