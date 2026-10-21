"""The churning channels tables' storage parameters are the same in the models
(what the e2e schema is built from) and in migration ``0058_channels`` (what
is deployed): a test run on one and production on the other would measure a
different table."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from app.modules.channels.infrastructure.storage import STORAGE

_MIGRATION = (
    Path(__file__).resolve().parents[5]
    / "migrations"
    / "versions"
    / "2026-10-17_channels_0058.py"
)


def _migration_storage() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("channels_0058", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found = re.findall(r"ALTER TABLE (\w+) SET \((.+?)\)\s*(?:;|$)", module._STORAGE)
    return dict(found)


def test_the_models_and_the_migration_tune_the_same_tables_the_same_way() -> None:
    assert _migration_storage() == STORAGE
