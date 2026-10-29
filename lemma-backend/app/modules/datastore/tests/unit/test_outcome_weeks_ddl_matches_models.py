"""Migration ``0066_outcome_weeks`` creates the week rows and the sweep's index
exactly as the models declare them. The e2e suites build their schema from the
models; a deploy runs the migration. A column, constraint or index in one and
not the other is a difference every test passes over."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.infrastructure.db.base import Base
from app.modules.test_support.e2e_base import _import_e2e_models

_MIGRATION = (
    Path(__file__).resolve().parents[5]
    / "migrations"
    / "versions"
    / "2026-10-26_outcome_weeks_0066.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("outcome_weeks_0066", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normal(ddl: str) -> str:
    text = " ".join(ddl.split())
    return re.sub(r"\s*([(),])\s*", r"\1", text)


def test_the_week_table_is_the_models_table():
    _import_e2e_models()
    model = Base.metadata.tables["pod_outcome_weeks"]
    compiled = CreateTable(model).compile(dialect=postgresql.dialect())
    assert _normal(_migration().TABLE) == _normal(str(compiled))
    assert not model.indexes, "a new index needs adding to the migration too"


def test_the_sweeps_index_is_the_models_index():
    _import_e2e_models()
    (index,) = [
        index
        for index in Base.metadata.tables["pods"].indexes
        if index.name == "ix_pods_with_measure"
    ]
    compiled = CreateIndex(index).compile(dialect=postgresql.dialect())
    assert _normal(_migration().INDEX) == _normal(str(compiled))
