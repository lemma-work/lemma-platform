"""Migration ``0074_machines`` creates the machine tables and columns exactly as
the models declare them. The e2e suites build their schema from the models; a
deploy runs the migration. A difference between the two is one every test
passes over.

``0078_vault_cutover`` since added a vault reference to two of the tables and
relaxed an old column; the models are compared with those changes taken back
out, as that migration states them."""

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
_MIGRATION = _VERSIONS / "2026-10-28_machines_0074.py"
_VAULT_CUTOVER = _VERSIONS / "2026-11-25_vault_cutover_0078.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration() -> ModuleType:
    return _load(_MIGRATION, "machines_0074")


def _before_vault_cutover(table: str, ddl: str) -> str:
    """``ddl`` (normalised) without what ``0078_vault_cutover`` added to ``table``."""
    for owner in _load(_VAULT_CUTOVER, "vault_cutover_0078")._OWNERS:
        if owner.table != table:
            continue
        column = owner.secret_column
        ddl = (
            ddl.replace(f"{column} UUID,", "")
            .replace(f",UNIQUE({column})", "")
            .replace(f",FOREIGN KEY({column})REFERENCES vault_secrets(id)", "")
        )
        for relaxed in owner.relax:
            ddl = ddl.replace(f"{relaxed} TEXT,", f"{relaxed} TEXT NOT NULL,")
    return ddl


def _normal(ddl: str) -> str:
    text = " ".join(ddl.split())
    return re.sub(r"\s*([(),])\s*", r"\1", text)


def _from_models(table: str) -> set[str]:
    _import_e2e_models()
    model = Base.metadata.tables[table]
    dialect = postgresql.dialect()
    created = _normal(str(CreateTable(model).compile(dialect=dialect)))
    return {_before_vault_cutover(table, created)} | {
        _normal(str(CreateIndex(index).compile(dialect=dialect)))
        for index in model.indexes
    }


@pytest.mark.parametrize("table", list(_migration().TABLES))
def test_the_migration_creates_the_table_the_models_declare(table: str) -> None:
    created = {_normal(statement) for statement in _migration().TABLES[table]}

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
