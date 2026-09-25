"""The shipped-migration half of scripts/check_migration_order.py.

A Desktop nightly runs ``alembic upgrade head`` on real machines, so a
migration is shipped the moment a nightly carries it. Editing or deleting it
afterwards strands those machines on a schema the code no longer describes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit


def _load_checker() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[4] / "scripts" / "check_migration_order.py"
    )
    spec = importlib.util.spec_from_file_location("check_migration_order", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()

SHIPPED = 'revision = "0040_x"\n\ndef upgrade():\n    op.add_column("t", c)\n'


def test_an_unchanged_shipped_migration_passes() -> None:
    current = {"0040_x": SHIPPED.replace("\n", "\r\n") + "   \n"}
    assert (
        checker.shipped_edits(current, {"0040_x": SHIPPED}, "desktop-nightly-a") == []
    )


def test_editing_a_shipped_migration_is_refused() -> None:
    current = {"0040_x": SHIPPED.replace('"t"', '"u"')}
    problems = checker.shipped_edits(current, {"0040_x": SHIPPED}, "desktop-nightly-a")
    assert len(problems) == 1
    assert "edited after it shipped in desktop-nightly-a" in problems[0]


def test_deleting_a_shipped_migration_is_refused() -> None:
    problems = checker.shipped_edits({}, {"0040_x": SHIPPED}, "v0.8.0")
    assert len(problems) == 1
    assert "no longer here" in problems[0]


def test_new_migrations_after_the_shipped_ones_are_fine() -> None:
    current = {"0040_x": SHIPPED, "0041_y": 'revision = "0041_y"\n'}
    assert checker.shipped_edits(current, {"0040_x": SHIPPED}, "v0.8.0") == []
