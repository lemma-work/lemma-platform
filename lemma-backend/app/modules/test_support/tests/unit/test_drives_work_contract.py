"""An e2e test that drives the work runtime in-process must hold the session worker.

``run_until_idle``, ``make_due`` and ``dispatcher_for`` claim items from the same
tables the session worker subprocess claims from. A test that uses them without
``pytest.mark.drives_work`` passes alone and fails once any module packed beside
it starts the worker -- the worker takes the item, runs it without the test's
doubles, and the test sees nothing (see
``app/modules/test_support/e2e/worker_hold.py``). Shard order decides it, so a
green local run proves nothing; this check does not depend on order.

A test drives work if it calls one of those helpers, or a function -- in its own
module or a sibling support module -- that does. It is exempt when it runs the
real worker on purpose: it takes ``worker``/``document_worker``, or its module
starts its own with ``production_worker_process``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[4]
_DRIVERS = frozenset({"run_until_idle", "make_due", "dispatcher_for"})
_REAL_WORKER_FIXTURES = frozenset({"worker", "document_worker"})

Function = ast.FunctionDef | ast.AsyncFunctionDef


def _called(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        if isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.add(call.func.attr)
    return names


def _driving(functions: list[Function], known: set[str]) -> set[str]:
    """``known`` plus every function here that reaches one of them."""
    driving = set(known)
    grew = True
    while grew:
        grew = False
        for function in functions:
            if function.name not in driving and _called(function) & driving:
                driving.add(function.name)
                grew = True
    return driving


def _functions(tree: ast.Module) -> list[Function]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _support_drivers(module: str, cache: dict[str, set[str]]) -> set[str]:
    if module not in cache:
        path = _APP.parent / (module.replace(".", "/") + ".py")
        if "/tests/" not in path.as_posix() or not path.is_file():
            cache[module] = set()
        else:
            tree = ast.parse(path.read_text())
            cache[module] = _driving(_functions(tree), set(_DRIVERS)) - _DRIVERS
    return cache[module]


def _marked(node: ast.AST) -> bool:
    return "drives_work" in ast.unparse(node)


def _unheld_drivers(path: Path, cache: dict[str, set[str]]) -> list[str]:
    source = path.read_text()
    tree = ast.parse(source)
    if "production_worker_process" in source:
        return []
    module_marked = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
        and _marked(node.value)
        for node in tree.body
    )
    if module_marked:
        return []
    known = set(_DRIVERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            drivers = _support_drivers(node.module, cache)
            known |= {alias.asname or alias.name for alias in node.names} & drivers
    driving = _driving(_functions(tree), known)
    unheld: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_") or not _called(node) & driving:
            continue
        if {a.arg for a in node.args.args} & _REAL_WORKER_FIXTURES:
            continue
        if not any(_marked(d) for d in node.decorator_list):
            unheld.append(f"{path.relative_to(_APP.parent)}::{node.name}")
    return unheld


def test_every_in_process_work_driver_holds_the_session_worker() -> None:
    cache: dict[str, set[str]] = {}
    unheld = [
        test
        for path in sorted(_APP.rglob("tests/e2e/**/test_*.py"))
        for test in _unheld_drivers(path, cache)
    ]
    assert not unheld, (
        "these e2e tests claim work in-process but are not marked "
        "`pytest.mark.drives_work`, so the session worker races them:\n"
        + "\n".join(unheld)
    )
