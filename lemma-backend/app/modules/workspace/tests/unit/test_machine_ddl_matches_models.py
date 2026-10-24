"""Migration ``0064_machines`` creates the machine tables and columns exactly as
the models declare them. The e2e suites build their schema from the models; a
deploy runs the migration. A difference between the two is one every test
passes over."""

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

_MIGRATION = (
    Path(__file__).resolve().parents[5]
    / "migrations"
    / "versions"
    / "2026-10-25_machines_0064.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("machines_0064", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    migration = _migration()
    return {**migration.TABLES, **migration.CONNECTED_MACHINE_TABLES}


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
