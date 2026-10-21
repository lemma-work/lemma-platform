"""Migration ``0058_channels`` creates the channels tables exactly as the
models declare them, and each later migration adds its column exactly as the
model declares it. The e2e suites build their schema from the models; a
deploy runs the migrations. A column, constraint or index in one and not the
other is a difference every test passes over."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.infrastructure.db.base import Base
from app.modules.test_support.e2e_base import _import_e2e_models

_VERSIONS = Path(__file__).resolve().parents[5] / "migrations" / "versions"
_MIGRATION = _VERSIONS / "2026-10-17_channels_0058.py"
#: The migrations after 0058 that add a column to one of its tables: each
#: names it as ``TABLE`` and ``COLUMN`` (its DDL).
_ADDED_LATER = (_VERSIONS / "2026-10-21_answers_shared_0061.py",)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration():
    return _load(_MIGRATION)


def _added_later(table: str) -> list[str]:
    """The columns later migrations add to ``table``, as their DDL."""
    return [
        _normal(module.COLUMN)
        for module in map(_load, _ADDED_LATER)
        if module.TABLE == table
    ]


def _normal(ddl: str) -> str:
    text = " ".join(ddl.split())
    return re.sub(r"\s*([(),])\s*", r"\1", text)


def _statements_for(table: str, ddl: str) -> set[str]:
    """The migration's CREATE TABLE and CREATE INDEX statements for ``table``."""
    found: set[str] = set()
    for statement in ddl.split(";"):
        normal = _normal(statement)
        if re.match(rf"CREATE TABLE {table}\(", normal) or re.match(
            rf"CREATE (UNIQUE )?INDEX \w+ ON {table}( USING \w+)?\(", normal
        ):
            found.add(normal)
    return found


def _from_models(table: str) -> set[str]:
    """The models' statements for ``table``, without the columns a later
    migration adds -- each of those must be in them, exactly as declared."""
    # Every model the e2e schema is built from, so foreign keys resolve.
    _import_e2e_models()
    model = Base.metadata.tables[table]
    dialect = postgresql.dialect()
    create = _normal(str(CreateTable(model).compile(dialect=dialect)))
    for column in _added_later(table):
        assert column + "," in create, f"{table} declares no {column}"
        create = create.replace(column + ",", "", 1)
    return {create} | {
        _normal(str(CreateIndex(index).compile(dialect=dialect)))
        for index in model.indexes
    }


@pytest.mark.parametrize("table", _migration()._NEW_TABLE_NAMES)
def test_the_migration_creates_the_table_the_models_declare(table: str) -> None:
    assert _statements_for(table, _migration()._NEW_TABLES) == _from_models(table)
