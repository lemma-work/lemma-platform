"""Migration ``0060_slack_apps`` creates the Slack tables exactly as the models
declare them. The e2e suites build their schema from the models; a deploy runs
the migration. A column, constraint or index in one and not the other is a
difference every test passes over."""

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
    / "2026-10-20_slack_apps_0060.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("slack_apps_0060", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normal(ddl: str) -> str:
    text = " ".join(ddl.split())
    return re.sub(r"\s*([(),])\s*", r"\1", text)


def _statements_for(table: str, ddl: str) -> set[str]:
    found: set[str] = set()
    for statement in ddl.split(";"):
        normal = _normal(statement)
        if re.match(rf"CREATE TABLE {table}\(", normal) or re.match(
            rf"CREATE (UNIQUE )?INDEX \w+ ON {table}( USING \w+)?\(", normal
        ):
            found.add(normal)
    return found


def _from_models(table: str) -> set[str]:
    _import_e2e_models()
    model = Base.metadata.tables[table]
    dialect = postgresql.dialect()
    return {_normal(str(CreateTable(model).compile(dialect=dialect)))} | {
        _normal(str(CreateIndex(index).compile(dialect=dialect)))
        for index in model.indexes
    }


@pytest.mark.parametrize("table", _migration()._NEW_TABLE_NAMES)
def test_the_migration_creates_the_table_the_models_declare(table: str) -> None:
    assert _statements_for(table, _migration()._NEW_TABLES) == _from_models(table)
