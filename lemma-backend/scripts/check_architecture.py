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
# How many classes an object is made of, counting itself. Above this it is worth
# naming in the baseline. Deliberately the size of the ancestry rather than the
# length of the longest chain: eight mixins side by side are a chain two deep and
# an object made of nine classes, and it is the nine a reader has to hold.
#
# Counted only for an object that is *assembled* -- one where some class in the
# ancestry, itself included, declares more than one base. A single-inheritance
# chain is depth and not composition, and a reader holds one link of it at a
# time: `AgentSurfaceNumberPoolExhaustedError -> AgentSurfaceError ->
# AppDomainError -> Exception` is four classes and no burden at all.
#
# Without that condition the metric was a tax on ordinary code rather than a
# signal. It named 274 classes, and the top of the list was 46 ORM models on
# `UUIDAuditBase`, 16 aggregates and every error hierarchy in the backend --
# so *any* new table or error type failed a ratchet that only fails on growth,
# and the fix was always to re-record the baseline, which is how a gate stops
# meaning anything. With it, 12 classes are named, and they are the mixin piles
# the rule was written for: `AgentSurfaceService`, the platform adapters, the
# progress observer, the repositories.
MAX_ANCESTRY = 3
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


class _ClassShape(ast.NodeVisitor):
    """What a class reaches for that it never declared, and how deep it inherits.

    `MAX_FILE_LINES` caps the page and `MAX_COMPLEXITY` caps the function. Both
    are satisfied by splitting, and that is how one class here reached ninety-two
    methods across fourteen files while recording zero violations of either:
    every file sat under six hundred lines, every method under the complexity
    cap, and the object they compose was never measured at all. `services/` grew
    to eighty-six files under exactly that incentive.

    `undeclared_self_attributes` is the number splitting cannot improve. A mixin
    that reads `self.surface_repository` without declaring it is not a unit; it
    is a fragment of some other object, and moving it into a file of its own
    makes this worse rather than better. A class with a constructor scores zero.

    Classes with a base this pass cannot resolve -- `BaseModel`, `Protocol`,
    anything from a library -- are skipped rather than guessed at, because their
    attributes come from a metaclass we cannot read and every one of them would
    count as undeclared. That exemption costs nothing here: the shape this
    measures is mixins, which have no bases at all.
    """

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.classes: dict[str, dict[str, Any]] = {}
        #: Local name -> the module it was imported from, for this file. Shared
        #: by reference with every class entry below, so it is complete by the
        #: time resolution reads it however late in the file an import sits.
        self.imported: dict[str, str] = {}

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Absolute only. A relative import would have to be resolved against
        # this file's package to name a module, and it lands in that same
        # package by construction -- which the same-package tie-breaker in
        # `resolve` already covers. There are 23 of them against 9,617
        # absolute, so the resolution they would add is not worth carrying a
        # package calculation for.
        if node.module and not node.level:
            for alias in node.names:
                self.imported[alias.asname or alias.name] = node.module
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        declared: set[str] = set()
        read: set[str] = set()
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                declared.add(statement.target.id)
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                declared.add(statement.name)
        for child in ast.walk(node):
            if not isinstance(child, ast.Attribute):
                continue
            if not isinstance(child.value, ast.Name) or child.value.id != "self":
                continue
            if isinstance(child.ctx, (ast.Store, ast.Del)):
                declared.add(child.attr)
            else:
                read.add(child.attr)
        # Keyed by file *and* name. Keyed by name alone, the sixteen duplicate
        # class names in this tree collided: `AgentRepository` is a Protocol in
        # `agent/domain/ports.py` and a concrete class in
        # `agent/infrastructure/repositories/`, and whichever was visited last
        # replaced the other -- so one of them stopped being measured at all,
        # and anything inheriting the name resolved through whichever won.
        self.classes[f"{self.relative_path}:{node.name}"] = {
            "name": node.name,
            "key": f"{self.relative_path}:{node.name}",
            "bases": [base.id for base in node.bases if isinstance(base, ast.Name)],
            "unresolved_bases": len(node.bases)
            - len([base for base in node.bases if isinstance(base, ast.Name)]),
            "declared": declared,
            "read": read,
            "imports": self.imported,
        }
        self.generic_visit(node)


def _class_shapes(
    classes: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    """Undeclared reads and ancestry size, resolved across the whole app.

    Entries are keyed by ``path:name``; a base, written in source, is only a
    name. So base resolution goes through an index built here, and a name held
    by more than one class is never resolved to whichever file happened to be
    walked last.

    Refusing to resolve an ambiguous name outright is the safe reading, but it
    is not a free one, and the cost runs the wrong way: the chain simply stops,
    the class measures shallower than it is, and **the ratchet only fails on
    growth**, so the smaller number becomes the new floor. A second
    ``DomainEvent`` added anywhere in the tree would silently drop sixty-six
    units of measured depth across thirty-five subclasses and report success.
    Ambiguity that costs coverage quietly is how a gate stops measuring the
    thing it was added for.

    So an ambiguous name is disambiguated by evidence, in descending order of
    how much the evidence is worth: an actual ``from X import Base`` in the
    asking file, then a definition in that same file, then one in the same
    package. Only the first is proof; the other two are proximity, and they are
    tie-breakers rather than the rule. A name with none of the three stays
    unresolved, which for `undeclared_self_attributes` means the class is
    skipped -- the same rule that skips anything inheriting a pydantic model or
    a Protocol.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in classes.values():
        by_name.setdefault(entry["name"], []).append(entry)

    def _path_of(entry: dict[str, Any]) -> str:
        return entry["key"].rsplit(":", 1)[0]

    def _defines(entry: dict[str, Any], module: str) -> bool:
        """Is this candidate the class ``module`` names?"""
        stem = module.replace(".", "/")
        path = _path_of(entry)
        return path.endswith((f"{stem}.py", f"{stem}/__init__.py"))

    def resolve(base: str, asking: dict[str, Any]) -> dict[str, Any] | None:
        found = by_name.get(base)
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        module = asking["imports"].get(base)
        if module:
            imported = [entry for entry in found if _defines(entry, module)]
            if len(imported) == 1:
                return imported[0]
        here = _path_of(asking)
        same_file = [entry for entry in found if _path_of(entry) == here]
        if len(same_file) == 1:
            return same_file[0]
        package = here.rsplit("/", 1)[0]
        same_package = [
            entry for entry in found if _path_of(entry).rsplit("/", 1)[0] == package
        ]
        if len(same_package) == 1:
            return same_package[0]
        return None

    def inherited(entry: dict[str, Any], seen: frozenset[str]) -> set[str]:
        if entry["key"] in seen:
            return set()
        names = set(entry["declared"])
        for base in entry["bases"]:
            found = resolve(base, entry)
            if found is not None:
                names |= inherited(found, seen | {entry["key"]})
        return names

    def ancestry(entry: dict[str, Any], seen: frozenset[str]) -> tuple[set[str], bool]:
        """Every class this one is made of, and whether any of them is assembled.

        The second half is what separates composition from depth -- see
        `MAX_ANCESTRY`. True as soon as one class in the chain declares more
        than one base, because that is the point where a reader stops being able
        to follow a single line.
        """
        if entry["key"] in seen:
            return set(), False
        found: set[str] = set()
        assembled = len(entry["bases"]) > 1
        for base in entry["bases"]:
            found.add(base)
            resolved = resolve(base, entry)
            if resolved is not None:
                inherited_names, inherited_assembled = ancestry(
                    resolved, seen | {entry["key"]}
                )
                found |= inherited_names
                assembled = assembled or inherited_assembled
        return found, assembled

    undeclared: dict[str, int] = {}
    deep: dict[str, int] = {}
    for entry in classes.values():
        ancestors, assembled = ancestry(entry, frozenset())
        made_of = 1 + len(ancestors)
        if made_of > MAX_ANCESTRY and assembled:
            deep[entry["key"]] = made_of
        if entry["unresolved_bases"] or any(
            resolve(base, entry) is None for base in entry["bases"]
        ):
            continue
        known = set(entry["declared"])
        for base in entry["bases"]:
            found = resolve(base, entry)
            if found is not None:
                known |= inherited(found, frozenset({entry["key"]}))
        missing = entry["read"] - known
        if missing:
            undeclared[entry["key"]] = len(missing)
    return undeclared, deep


def snapshot() -> dict[str, Any]:
    forbidden: dict[str, int] = defaultdict(int)
    dependency_graph: dict[str, set[str]] = defaultdict(set)
    oversized: dict[str, int] = {}
    complex_functions: dict[str, int] = {}
    broad_catches: dict[str, int] = {}
    untyped_escapes: dict[str, int] = {}
    core_module_imports: dict[str, int] = defaultdict(int)
    classes: dict[str, dict[str, Any]] = {}

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

        shapes = _ClassShape(relative)
        shapes.visit(tree)
        classes.update(shapes.classes)

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

    undeclared_self, deep_inheritance = _class_shapes(classes)
    return {
        "forbidden_imports": dict(sorted(forbidden.items())),
        "core_module_imports": dict(sorted(core_module_imports.items())),
        "module_cycles": [list(cycle) for cycle in _cycles(dependency_graph)],
        "oversized_files": dict(sorted(oversized.items())),
        "complex_functions": _aggregate_by_module(complex_functions),
        "broad_catches": _aggregate_by_module(broad_catches),
        "untyped_escapes": _aggregate_by_module(untyped_escapes),
        "undeclared_self_attributes": _aggregate_by_module(undeclared_self),
        "ancestry_size": _aggregate_by_module(deep_inheritance),
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
        ("undeclared self attribute count", "undeclared_self_attributes"),
        ("ancestry size", "ancestry_size"),
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
