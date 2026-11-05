"""The old event transport is gone, and stays gone (design §13, PR 3 removal).

No first-party module imports FastStream or streaq, neither is installed, and
nothing names the deleted packages -- so a stray import fails here rather than
at a worker's boot.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
from app.modules.test_support import source_tree

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[4]
_GONE = (
    "faststream",
    "streaq",
    "app.events",
    "app.core.infrastructure.events",
    "app.core.infrastructure.jobs",
    "app.core.domain.message_bus",
    "app.core.domain.job_queue",
)


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(source_tree.tree(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


def test_nothing_imports_the_old_transport() -> None:
    offenders = []
    for root in ("app", "migrations", "scripts"):
        for path in source_tree.python_files(BACKEND / root):
            for name in _imports(path):
                if any(name == gone or name.startswith(f"{gone}.") for gone in _GONE):
                    offenders.append(f"{path.relative_to(BACKEND)}: {name}")
    assert not offenders, offenders


def test_neither_library_is_installed_or_locked() -> None:
    assert importlib.util.find_spec("faststream") is None
    assert importlib.util.find_spec("streaq") is None
    lock = (BACKEND / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "faststream"' not in lock
    assert 'name = "streaq"' not in lock
