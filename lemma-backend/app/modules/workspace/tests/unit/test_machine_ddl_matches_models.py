"""Migrations ``0074_machines`` and ``0075_machine_connection_pairing`` create
the machine tables and columns exactly as the models declare them. The e2e
suites build their schema from the models; a deploy runs the migrations. A
difference between the two is one every test passes over."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.infrastructure.db.base import Base
from app.modules.test_support.e2e_base import _import_e2e_models

_VERSIONS = Path(__file__).resolve().parents[5] / "migrations" / "versions"
_MACHINES = "2026-10-28_machines_0074.py"
_PAIRING = "2026-11-19_machine_connection_pairing_0075.py"
#: 0075 changes ``machine_connections`` after 0074 created it, so 0074's
#: CREATE TABLE is the shipped history, not today's model; 0075's columns are
#: pinned to the model one by one instead.
_ALTERED_LATER = {"machine_connections"}


def _load(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(filename[:-3], _VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration() -> ModuleType:
    return _load(_MACHINES)


def _pairing() -> ModuleType:
    return _load(_PAIRING)


def _normal(ddl: str) -> str:
    text = " ".join(ddl.split())
    return re.sub(r"\s*([(),])\s*", r"\1", text)


def _from_models(table: str) -> set[str]:
    _import_e2e_models()
    model = Base.metadata.tables[table]
    dialect = postgresql.dialect()
    return {_normal(str(CreateTable(model).compile(dialect=dialect)))} | {
        _normal(str(CreateIndex(index).compile(dialect=dialect)))
        for index in model.indexes
    }


def _tables() -> dict[str, tuple[str, ...]]:
    created = {**_migration().TABLES, **_pairing().TABLES}
    return {
        table: statements
        for table, statements in created.items()
        if table not in _ALTERED_LATER
    }


@pytest.mark.parametrize("table", list(_tables()))
def test_the_migration_creates_the_table_the_models_declare(table: str) -> None:
    created = {_normal(statement) for statement in _tables()[table]}

    assert created == _from_models(table)


def _column_ddl(table: str, column: str) -> str:
    _import_e2e_models()
    model_column = Base.metadata.tables[table].c[column]
    compiled = model_column.type.compile(dialect=postgresql.dialect())
    default = model_column.server_default
    parts = [column, compiled]
    if default is not None:
        parts.append(f"DEFAULT {default.arg.text}")  # type: ignore[attr-defined]
    if not model_column.nullable:
        parts.append("NOT NULL")
    return " ".join(parts)


def test_the_sandbox_columns_are_the_models():
    added = {_normal(column) for column in _migration().SANDBOX_COLUMNS}
    declared = {
        _normal(_column_ddl("sandboxes", column.split()[0]))
        for column in _migration().SANDBOX_COLUMNS
    }

    assert added == declared


def test_the_agent_column_is_the_models():
    assert _normal(_migration().AGENT_COLUMN) == _normal(
        _column_ddl("agents", "machine")
    )


@pytest.mark.parametrize("table", list(_migration().LIFECYCLE_TABLES))
def test_the_lifecycle_tables_are_the_models(table: str) -> None:
    created = {_normal(statement) for statement in _migration().LIFECYCLE_TABLES[table]}

    assert created == _from_models(table)


def test_the_lifecycle_sandbox_columns_are_the_models():
    added = {_normal(column) for column in _migration().LIFECYCLE_SANDBOX_COLUMNS}
    declared = {
        _normal(_column_ddl("sandboxes", column.split()[0]))
        for column in _migration().LIFECYCLE_SANDBOX_COLUMNS
    }

    assert added == declared


def test_the_connection_columns_pairing_adds_are_the_models():
    added = {_normal(column) for column in _pairing().CONNECTION_COLUMNS}
    declared = {
        _normal(_column_ddl("machine_connections", column.split()[0]))
        for column in _pairing().CONNECTION_COLUMNS
    }

    assert added == declared


@pytest.mark.parametrize("column", _pairing().NOW_NULLABLE)
def test_the_connection_columns_pairing_relaxes_are_nullable_in_the_models(
    column: str,
) -> None:
    _import_e2e_models()

    assert Base.metadata.tables["machine_connections"].c[column].nullable
