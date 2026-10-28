"""Outside ``agent/agent_host/``, the Agent Host is reached only through its api.

The sub-package has its own domain, link, control plane and harness; what the
rest of the backend may use of them is the short list in ``agent_host/api.py``.
An import of any other file in it from outside -- a repository, a link helper,
a harness internal -- is how the five directories it was gathered from grew
their cross-dependencies, so it fails here. Tests and test support may reach
inside: they are where its internals are checked.

The api's names resolve lazily, so the second check holds its two halves
together: every name it declares for the type checker is one it can resolve,
and resolves to the object the declaring module defines.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest

from app.modules.agent.agent_host import api

pytestmark = pytest.mark.unit

_APP = Path(__file__).resolve().parents[4]
_PACKAGE = "app.modules.agent.agent_host"
_INSIDE = _APP / "modules" / "agent" / "agent_host"
_API = f"{_PACKAGE}.api"


def _production_sources() -> Iterator[Path]:
    for path in sorted(_APP.rglob("*.py")):
        parts = path.relative_to(_APP).parts
        if "tests" in parts or parts[:2] == ("modules", "test_support"):
            continue
        if path.is_relative_to(_INSIDE):
            continue
        yield path


def _imported_modules(tree: ast.Module) -> Iterator[tuple[int, str]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.lineno, node.module
            if node.module == _PACKAGE:
                for alias in node.names:
                    yield node.lineno, f"{_PACKAGE}.{alias.name}"


def test_nothing_outside_agent_host_imports_past_its_api() -> None:
    reaching_in = [
        f"{path.relative_to(_APP.parent)}:{line} imports {module}"
        for path in _production_sources()
        for line, module in _imported_modules(ast.parse(path.read_text()))
        if module.startswith(f"{_PACKAGE}.") and module != _API
    ]
    assert not reaching_in, (
        "Import the Agent Host through agent_host/api.py (add the name there):\n"
        + "\n".join(reaching_in)
    )


def _declared_for_the_type_checker() -> dict[str, str]:
    tree = ast.parse(Path(api.__file__).read_text())
    declared: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == (
            "TYPE_CHECKING"
        ):
            for statement in node.body:
                assert isinstance(statement, ast.ImportFrom) and statement.module
                for alias in statement.names:
                    declared[alias.asname or alias.name] = statement.module
    return declared


def test_the_api_declares_exactly_what_it_resolves() -> None:
    declared = _declared_for_the_type_checker()
    resolved = {name: f"{_PACKAGE}.{home}" for name, home in api._EXPORTS.items()}
    assert declared == resolved
    for name, module in resolved.items():
        assert getattr(api, name) is getattr(import_module(module), name)
