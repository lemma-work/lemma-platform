"""Every re-exported e2e fixture must bring its dependencies with it.

Module conftests opt into shared fixtures by rebinding them one name at a time
(``worker = e2e_fixtures.worker``). pytest resolves a fixture's *arguments*
against the requesting test's conftest chain, not against the module the
fixture was defined in, so rebinding a fixture without also rebinding what it
takes fails with a bare "fixture not found" -- and only for the modules that
missed it, only once an e2e run reaches them.

That is a slow and misleading way to find a one-line omission, so the binding
graph is checked here instead.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.modules.test_support.e2e import fixtures as e2e_fixtures

MODULES_ROOT = Path(__file__).resolve().parents[2]


def _conftests() -> list[Path]:
    return sorted(MODULES_ROOT.glob("*/tests/e2e/conftest.py"))


def _rebound_names(tree: ast.Module) -> set[str]:
    """Names bound as ``<name> = e2e_fixtures.<name>`` at module level."""
    bound: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Attribute):
            continue
        if isinstance(value.value, ast.Name) and value.value.id == "e2e_fixtures":
            bound.add(target.id)
    return bound


def _locally_defined(tree: ast.Module) -> set[str]:
    """Fixtures the conftest defines itself, which need no rebinding."""
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _fixture_dependencies(name: str) -> set[str]:
    """Parameters of a shared fixture that are themselves shared fixtures."""
    fixture = getattr(e2e_fixtures, name)
    func = getattr(fixture, "__wrapped__", fixture)
    try:
        parameters = inspect.signature(func).parameters
    except TypeError, ValueError:
        return set()
    return {
        parameter
        for parameter in parameters
        if hasattr(e2e_fixtures, parameter) and parameter != "request"
    }


@pytest.mark.parametrize("conftest", _conftests(), ids=lambda p: p.parts[-4])
def test_rebound_fixtures_also_rebind_their_dependencies(conftest: Path) -> None:
    tree = ast.parse(conftest.read_text(encoding="utf-8"))
    available = _rebound_names(tree) | _locally_defined(tree)

    missing: list[str] = []
    for name in _rebound_names(tree):
        for dependency in _fixture_dependencies(name) - available:
            missing.append(f"{name} needs {dependency}")

    assert not missing, (
        f"{conftest.relative_to(MODULES_ROOT.parent)} rebinds fixtures without "
        f"their dependencies: {sorted(missing)}. Add "
        f"`<dep> = e2e_fixtures.<dep>` for each."
    )
