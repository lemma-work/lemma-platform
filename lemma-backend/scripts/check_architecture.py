#!/usr/bin/env python3
"""Enforce backend architecture and maintainability ratchets.

The baseline records pre-existing debt, not exemptions for new code. CI fails
when a new cross-module internal import/cycle appears, a broad catch is added,
or a large/complex function grows. Shrinking the baseline is always allowed.

Three metrics used to live here and no longer do. `composition_deep_imports`,
`module_composition_imports` and `induced_module_cycles` all measured
`app/composition`, a shared middle layer that thirteen of fifteen modules
depended on. They did their job: the directory was emptied and deleted, all
three read zero, and `_inline_composition` -- which existed to report what the
graph would look like once the hop was gone -- had nothing left to inline, so
`induced_module_cycles` was `module_cycles` computed twice.

They are gone rather than kept at zero because a metric that cannot move is not
a ratchet; it is a line in a report that a reader has to work out is dead.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULES_ROOT = ROOT / "app" / "modules"
# The metrics below cover the whole application package, not only app/modules/.
# They used to stop at the module tree, which is how app/core/ grew a 975-line
# file and several hundred untyped escapes without any of it being counted: the
# gate that exists to stop growth could not see the two places the growth was.
APP_ROOT = ROOT / "app"
ALLOWED_PUBLIC_SURFACES = {"contracts"}
# app/core is what modules are built on, so it must not depend on them. The one
# legitimate importer is the registry, whose job is naming every module.
CORE_MODULE_IMPORT_EXEMPT = {"app/core/registry/installed.py"}
MAX_FILE_LINES = 600
MAX_COMPLEXITY = 15
# Generated files are exempt from the size rule. `event_catalog.py` is one line
# per logging event, emitted by scripts/generate_logging_event_catalogs.py, and
# it was already 128 lines over the limit -- so adding a single `logger.info`
# anywhere in the backend grew a baselined count and failed this gate on a file
# nobody wrote. Splitting it is not available either: the generator owns the
# whole file. The size rule exists to keep hand-written files readable, and this
# one is not read, it is regenerated.
GENERATED_FILES = {"app/core/log/event_catalog.py"}


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in APP_ROOT.rglob("*.py")
        if "tests" not in path.parts
        and "test_support" not in path.parts
        and "__pycache__" not in path.parts
    )


def _source_module(path: Path) -> str:
    """Name the bucket a file's metrics are counted under.

    `app/modules/agent/...` is `agent`; `app/core/...` is `core`; a file
    directly under `app/` is `app`. Keyed by package rather than by path depth so that a file moving
    between directories inside its own package does not churn the baseline.
    """
    parts = path.relative_to(APP_ROOT).parts
    if len(parts) < 2:
        return "app"
    if parts[0] == "modules":
        return parts[1] if len(parts) > 2 else "modules"
    return parts[0]


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


# Bare containers say "a collection of something" and stop there, which is the
# same abdication as `Any` wearing a different word.
_UNPARAMETERISED = frozenset({"dict", "list", "tuple", "set", "frozenset"})


class _UntypedEscapes(ast.NodeVisitor):
    """Count annotations that opt out of the type system.

    `Any` and a bare `dict` are how a boundary stops being checked. Some are
    unavoidable -- a provider's JSON really is unknown until it is validated --
    but each one is a place the type checker cannot help, and the number should
    only ever go down. Counted per file and aggregated per module, like the
    other metrics here, so the ratchet stays reviewable.

    Only annotations. An `Any` in a comment, a string, or a `cast` the code
    immediately narrows is not the thing being discouraged.
    """

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.count = 0

    def _inspect(self, annotation: ast.expr | None) -> None:
        """Walk an annotation, counting only what actually gives up.

        `dict[str, int]` must not count. Walking the tree naively sees the
        `dict` inside the subscript and reads a fully specified container as an
        escape -- which would inflate the baseline with the very thing the rule
        asks for, and leave the number meaning something other than what it
        says. So a subscript's own name is skipped and only its parameters are
        examined: `dict[str, Any]` counts once, for the `Any`.
        """
        if annotation is None:
            return
        if isinstance(annotation, ast.Subscript):
            # Parameterised: the container is specified, so only what it is
            # parameterised *with* can still be an escape.
            self._inspect(annotation.slice)
            return
        if isinstance(annotation, ast.Tuple):
            for element in annotation.elts:
                self._inspect(element)
            return
        if isinstance(annotation, ast.BinOp):  # `X | Y`
            self._inspect(annotation.left)
            self._inspect(annotation.right)
            return
        if isinstance(annotation, ast.Name):
            if annotation.id == "Any" or annotation.id in _UNPARAMETERISED:
                self.count += 1
            return
        if isinstance(annotation, ast.Attribute) and annotation.attr == "Any":
            self.count += 1
            return
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            # A stringised annotation. Parsing it keeps `"dict"` from hiding
            # behind quotes, and a fragment that will not parse is not one this
            # check should have an opinion about.
            try:
                self._inspect(ast.parse(annotation.value, mode="eval").body)
            except SyntaxError:
                return

    def visit_arg(self, node: ast.arg) -> None:
        self._inspect(node.annotation)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._inspect(node.annotation)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._inspect(node.returns)
        self.generic_visit(node)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node belonging to `node` itself, not to a function nested in it.

    `ast.walk` descends into nested `def`s and classes, so a handler inside a
    closure was counted once for the closure and again for each function
    enclosing it -- six double-counts in `agent_surfaces` alone, which is why
    95 real handlers there reported as 100. Complexity had the same bug from
    the same walk: an inner function's branches inflated its parent's score.
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(child))


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    """`except:`, `except Exception:` or `except BaseException:`."""
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id in {
        "Exception",
        "BaseException",
    }


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
        for child in _own_nodes(node):
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

        for child in _own_nodes(node):
            if isinstance(child, ast.ExceptHandler) and _is_broad_handler(child):
                self.broad_catches[key] += 1

        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Module(self, node: ast.Module) -> None:
        # A handler at module scope belongs to no function key, so it was
        # counted nowhere at all -- an optional-dependency `except Exception:`
        # around an import was invisible to this gate entirely.
        key = f"{self.relative_path}:<module>"
        for child in _own_nodes(node):
            if isinstance(child, ast.ExceptHandler) and _is_broad_handler(child):
                self.broad_catches[key] += 1
        self.generic_visit(node)


def snapshot() -> dict[str, Any]:
    forbidden: dict[str, int] = defaultdict(int)
    dependency_graph: dict[str, set[str]] = defaultdict(set)
    oversized: dict[str, int] = {}
    complex_functions: dict[str, int] = {}
    broad_catches: dict[str, int] = {}
    untyped_escapes: dict[str, int] = {}
    core_module_imports: dict[str, int] = defaultdict(int)

    for path in _python_files():
        relative = path.relative_to(ROOT).as_posix()
        source = _source_module(path)
        text = path.read_text(encoding="utf-8")
        line_count = len(text.splitlines())
        if line_count > MAX_FILE_LINES and relative not in GENERATED_FILES:
            oversized[relative] = line_count

        tree = ast.parse(text, filename=str(path))
        metrics = _FunctionMetrics(relative)
        metrics.visit(tree)
        complex_functions.update(metrics.complex)
        broad_catches.update(metrics.broad_catches)

        escapes = _UntypedEscapes(relative)
        escapes.visit(tree)
        if escapes.count:
            untyped_escapes[relative] = escapes.count

        in_modules = MODULES_ROOT in path.parents
        for node in ast.walk(tree):
            for imported in _imported_modules(node):
                parts = imported.split(".")
                if len(parts) < 3 or parts[:2] != ["app", "modules"]:
                    continue
                target = parts[2]
                if source == "core" and relative not in CORE_MODULE_IMPORT_EXEMPT:
                    core_module_imports[f"core->{target}"] += 1
                if not in_modules or target == source:
                    continue
                if not _allowed_cross_module_import(parts):
                    forbidden[f"{source}->{target}"] += 1
                    # Only internal reaches build the cycle graph. Two modules
                    # publishing contracts to each other is the target design,
                    # not a defect: `agent` reads surface capabilities and
                    # `agent_surfaces` reads a conversation context, both
                    # through published surfaces, and neither package imports
                    # the other -- so there is no import cycle to have. Counting
                    # those edges made the shape this refactor is heading for
                    # indistinguishable from the tangle it is leaving.
                    dependency_graph[source].add(target)

    return {
        "forbidden_imports": dict(sorted(forbidden.items())),
        "core_module_imports": dict(sorted(core_module_imports.items())),
        "module_cycles": [list(cycle) for cycle in _cycles(dependency_graph)],
        "oversized_files": dict(sorted(oversized.items())),
        "complex_functions": _aggregate_by_module(complex_functions),
        "broad_catches": _aggregate_by_module(broad_catches),
        "untyped_escapes": _aggregate_by_module(untyped_escapes),
    }


def _aggregate_by_module(values: dict[str, int]) -> dict[str, int]:
    """Keep the ratchet reviewable while retaining per-module growth signals."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for key, value in values.items():
        path = key.split(":", 1)[0]
        grouped[_source_module(ROOT / path)].append(value)
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

    for node in sorted(
        set(graph) | {item for values in graph.values() for item in values}
    ):
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
    for label, key in (("app/core importing a module", "core_module_imports"),):
        for name, (before, after) in _growth(
            current[key], baseline.get(key, {})
        ).items():
            failures.append(f"{label} grew: {name} ({before} -> {after})")
    for cycle in _new_pairs(
        current["module_cycles"], baseline.get("module_cycles", [])
    ):
        failures.append(f"new module cycle: {' -> '.join(cycle)}")
    for label, key in (
        ("oversized file", "oversized_files"),
        ("complex function", "complex_functions"),
        ("broad catch count", "broad_catches"),
        ("untyped escape count", "untyped_escapes"),
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
        f"{sum(current['core_module_imports'].values())} core->module imports, "
        f"{len(current['module_cycles'])} cycles)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
