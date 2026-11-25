"""Migration ``0060_slack_apps`` creates the Slack tables exactly as the models
declare them. The e2e suites build their schema from the models; a deploy runs
the migration. A column, constraint or index in one and not the other is a
difference every test passes over.

``0078_vault_cutover`` later changed one of them: it added the vault reference
and relaxed the old token columns to nullable. The models declare that later
state, so it is undone on their side before comparing with ``0060``; the vault
migration's own e2e test covers what it changed.
"""

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


#: What ``0078_vault_cutover`` did to these tables, as edits that undo it in
#: their normalised DDL: the reference it added, and the NOT NULLs it dropped.
_VAULT_CUTOVER = {
    "slack_configuration_tokens": (
        ("tokens_secret_id UUID,", ""),
        (",UNIQUE(tokens_secret_id)", ""),
        (",FOREIGN KEY(tokens_secret_id)REFERENCES vault_secrets(id)", ""),
        ("access_token TEXT,", "access_token TEXT NOT NULL,"),
        ("refresh_token TEXT,", "refresh_token TEXT NOT NULL,"),
    ),
}


def _as_of_0060(table: str, ddl: str) -> str:
    for before, after in _VAULT_CUTOVER.get(table, ()):
        assert before in ddl, f"{table}: 0078's change {before!r} is not declared"
        ddl = ddl.replace(before, after)
    return ddl


def _from_models(table: str) -> set[str]:
    _import_e2e_models()
    model = Base.metadata.tables[table]
    dialect = postgresql.dialect()
    created = _normal(str(CreateTable(model).compile(dialect=dialect)))
    return {_as_of_0060(table, created)} | {
        _normal(str(CreateIndex(index).compile(dialect=dialect)))
        for index in model.indexes
    }


@pytest.mark.parametrize("table", _migration()._NEW_TABLE_NAMES)
def test_the_migration_creates_the_table_the_models_declare(table: str) -> None:
    assert _statements_for(table, _migration()._NEW_TABLES) == _from_models(table)
