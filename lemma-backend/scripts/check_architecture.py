#!/usr/bin/env python3
"""Enforce backend architecture and maintainability ratchets.

The baseline records pre-existing debt, not exemptions for new code. CI fails
when a new cross-module internal import/cycle appears, a broad catch is added,
or a large/complex function grows. Shrinking the baseline is always allowed.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULES_ROOT = ROOT / "app" / "modules"
ALLOWED_PUBLIC_SURFACES = {"contracts"}
MAX_FILE_LINES = 600
MAX_COMPLEXITY = 15


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in MODULES_ROOT.rglob("*.py")
        if "tests" not in path.parts
        and "test_support" not in path.parts
        and "__pycache__" not in path.parts
    )


def _source_module(path: Path) -> str:
    return path.relative_to(MODULES_ROOT).parts[0]


def _allowed_cross_module_import(parts: list[str]) -> bool:
    if len(parts) < 4:
        return False
    surface = parts[3]
    if surface in ALLOWED_PUBLIC_SURFACES:
        return True
    return surface == "domain" and len(parts) >= 5 and parts[4] == "events"


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


class _FunctionMetrics(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.complex: dict[str, int] = {}
        self.broad_catches: dict[str, int] = defaultdict(int)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        key = f"{self.relative_path}:{'.'.join(self.scope)}"
        score = 1
        for child in ast.walk(node):
            if isinstance(
                child,
                (
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.IfExp,
                    ast.ExceptHandler,
                    ast.comprehension,
                    ast.Match,
                ),
            ):
                score += 1
            elif isinstance(child, ast.BoolOp):
                score += max(1, len(child.values) - 1)
        if score > MAX_COMPLEXITY:
            self.complex[key] = score

        for child in ast.walk(node):
            if not isinstance(child, ast.ExceptHandler):
                continue
            if child.type is None:
                self.broad_catches[key] += 1
            elif isinstance(child.type, ast.Name) and child.type.id in {
                "Exception",
                "BaseException",
            }:
                self.broad_catches[key] += 1

        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def snapshot() -> dict[str, Any]:
    forbidden: dict[str, int] = defaultdict(int)
    dependency_graph: dict[str, set[str]] = defaultdict(set)
    oversized: dict[str, int] = {}
    complex_functions: dict[str, int] = {}
    broad_catches: dict[str, int] = {}

    for path in _python_files():
        relative = path.relative_to(ROOT).as_posix()
        source = _source_module(path)
        text = path.read_text(encoding="utf-8")
        line_count = len(text.splitlines())
        if line_count > MAX_FILE_LINES:
            oversized[relative] = line_count

        tree = ast.parse(text, filename=str(path))
        metrics = _FunctionMetrics(relative)
        metrics.visit(tree)
        complex_functions.update(metrics.complex)
        broad_catches.update(metrics.broad_catches)

        for node in ast.walk(tree):
            for imported in _imported_modules(node):
                parts = imported.split(".")
                if len(parts) < 3 or parts[:2] != ["app", "modules"]:
                    continue
                target = parts[2]
                if target == source:
                    continue
                dependency_graph[source].add(target)
                if not _allowed_cross_module_import(parts):
                    forbidden[f"{source}->{target}"] += 1

    return {
        "forbidden_imports": dict(sorted(forbidden.items())),
        "module_cycles": [list(cycle) for cycle in _cycles(dependency_graph)],
        "oversized_files": dict(sorted(oversized.items())),
        "complex_functions": _aggregate_by_module(complex_functions),
        "broad_catches": _aggregate_by_module(broad_catches),
    }


def _aggregate_by_module(values: dict[str, int]) -> dict[str, int]:
    """Keep the ratchet reviewable while retaining per-module growth signals."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for key, value in values.items():
        path = key.split(":", 1)[0]
        parts = Path(path).parts
        module = parts[2] if len(parts) >= 3 else "core"
        grouped[module].append(value)
    result: dict[str, int] = {}
    for module, module_values in sorted(grouped.items()):
        result[f"{module}:count"] = len(module_values)
        result[f"{module}:total"] = sum(module_values)
        result[f"{module}:max"] = max(module_values)
    return result


def _cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Return normalized strongly connected components with at least 2 nodes."""
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in graph.get(node, set()):
            if neighbor not in indexes:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[neighbor])
        if lowlinks[node] != indexes[node]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for node in sorted(set(graph) | {item for values in graph.values() for item in values}):
        if node not in indexes:
            visit(node)
    return sorted(components)


def _new_pairs(current: list[list[str]], baseline: list[list[str]]) -> list[list[str]]:
    allowed = {tuple(item) for item in baseline}
    return [item for item in current if tuple(item) not in allowed]


def _growth(
    current: dict[str, int], baseline: dict[str, int]
) -> dict[str, tuple[int, int]]:
    return {
        key: (baseline.get(key, 0), value)
        for key, value in current.items()
        if value > baseline.get(key, 0)
    }


def check(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for name, (before, after) in _growth(
        current["forbidden_imports"], baseline.get("forbidden_imports", {})
    ).items():
        failures.append(f"forbidden import count grew: {name} ({before} -> {after})")
    for cycle in _new_pairs(current["module_cycles"], baseline.get("module_cycles", [])):
        failures.append(f"new module cycle: {' -> '.join(cycle)}")
    for label, key in (
        ("oversized file", "oversized_files"),
        ("complex function", "complex_functions"),
        ("broad catch count", "broad_catches"),
    ):
        for name, (before, after) in _growth(
            current[key], baseline.get(key, {})
        ).items():
            failures.append(f"{label} grew: {name} ({before} -> {after})")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "architecture-baseline.json",
    )
    parser.add_argument("--snapshot", action="store_true")
    args = parser.parse_args()
    current = snapshot()
    if args.snapshot:
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    failures = check(current, baseline)
    if failures:
        print("Architecture ratchet failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(
        "Architecture ratchet passed "
        f"({len(current['forbidden_imports'])} inherited import violations, "
        f"{len(current['module_cycles'])} inherited cycles)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
