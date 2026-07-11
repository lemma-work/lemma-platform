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


@pytest.mark.asyncio
async def test_schema_provisioning_takes_shared_bootstrap_lock_before_create() -> None:
    connection = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def begin():
        yield connection

    manager = SchemaManager(
        engine=SimpleNamespace(begin=begin),
        session_factory=object(),
    )
    pod_id = uuid4()

    await manager.create_datastore_schema(pod_id)

    assert connection.execute.await_count == 2
    lock_call, create_call = connection.execute.await_args_list
    assert "pg_advisory_xact_lock" in str(lock_call.args[0])
    assert lock_call.args[1] == {"schema_name": manager.get_schema_name(pod_id)}
    assert "CREATE SCHEMA IF NOT EXISTS" in str(create_call.args[0])
