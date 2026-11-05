"""Which layer of this module may import which.

The HTTP layer sits on top. Contracts are what other modules call, so a
contract that imported an API schema made every caller of it -- the pod-bundle
exporter among them -- depend on this module's HTTP layer, and services that
did the same could not be used without it. The checks read import statements,
so a violation fails here rather than in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_MODULE = Path(__file__).resolve().parents[2]
_PACKAGE = "app.modules.agent_surfaces"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _violations(layer: str, forbidden: str) -> list[str]:
    root = _MODULE / layer
    bad: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for module in _imports(path):
            if module == forbidden or module.startswith(f"{forbidden}."):
                bad.append(f"{path.relative_to(_MODULE)} imports {module}")
    return bad


@pytest.mark.parametrize(
    "layer", ["contracts", "domain", "services", "platforms", "infrastructure"]
)
def test_nothing_below_the_http_layer_imports_it(layer: str) -> None:
    assert _violations(layer, f"{_PACKAGE}.api") == []


@pytest.mark.parametrize("forbidden", ["services", "infrastructure", "api"])
def test_platform_adapters_reach_nothing_above_them(forbidden: str) -> None:
    """An adapter talks to its platform. Anything that composes services --
    a tool that checks a member's rights, then sends -- is a service, and
    living under `platforms/` hid an inverted dependency."""
    assert _violations("platforms", f"{_PACKAGE}.{forbidden}") == []


def test_the_http_layer_goes_through_services_not_repositories() -> None:
    """A route that builds a repository is a service with no name: its rules
    cannot be reached by anything that is not that route."""
    assert _violations("api", f"{_PACKAGE}.infrastructure.repositories") == []


def test_services_build_no_sql_themselves() -> None:
    """Queries live behind repository methods, where they are named and
    reusable, not as `select(...)` inside a service."""
    statements = {"select", "update", "delete", "insert"}
    building: list[str] = []
    for path in sorted((_MODULE / "services").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "sqlalchemy"
                and statements & {alias.name for alias in node.names}
            ):
                building.append(str(path.relative_to(_MODULE)))
    assert building == []
