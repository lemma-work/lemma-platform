#!/usr/bin/env python3
"""Verify every settings field a module names actually exists.

Settings objects are pydantic models, so a stale name raises at the moment it
is read -- not at import. A field that only some e2e path touches can therefore
survive a full unit run and a type check, and fail hours later on real
infrastructure. That is exactly what happened when the workspace fields moved
out of the core Settings: the harness set them by string key, and nothing
noticed until a Docker sandbox was already running.

Two rules, over all eleven module-level settings singletons rather than the two
this started with. The nine that were uncovered are the per-module classes a
field is most likely to be moved *into*, so leaving them out aimed the gate
away from the change it exists to survive.

``unknown-field``
    A name that is not a field, whether written as ``settings.<name>`` or
    reached through ``getattr``. A default argument does not excuse it:
    ``getattr(settings, "frontend_base_url", None)`` on a field that no longer
    exists is not a tolerant read, it is a branch that silently takes the
    fallback forever.

``dynamic-name``
    ``getattr``/``setattr``/``hasattr`` whose name is computed. Access written
    that way is invisible to grep, to a type checker and to the first rule,
    which is how a field can be read on every production boot and look unused
    everywhere else. Where the name resolves to string literals in the same
    module -- a workload-class table, a dict of harness overrides -- each one is
    checked like any other access. Where it does not, an f-string over a signal
    name being the only shape in the tree, there is nothing to check against:
    the site is recorded instead, so that a rename which cannot be verified is
    at least *listed* for whoever is moving fields between settings classes.

Both rules record against a ratcheted baseline, because the widening found live
problems and a gate nobody can make green gets deleted. The baseline may shrink
freely; growing it means a new field name has been put beyond checking.

Known blind spots, so their absence is a decision rather than an oversight:

* ``monkeypatch.setattr(settings, "field", ...)``. pytest raises on a missing
  attribute, so those call sites already police themselves -- except the ones
  passing ``raising=False``, which do not, and which this gate does not see.
* a settings object reached through anything but a bare local name:
  ``security.settings.x``, ``getattr(ConnectorSettings(), field)`` over a
  ``parametrize`` table, an instance held on ``self``.
* names assembled from runtime data -- an env var, a request field. Nothing
  static can reach those, and the tree has none today.

Usage::

    uv run python scripts/check_settings_attrs.py
    uv run python scripts/check_settings_attrs.py --update-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "settings-attrs-baseline.json"

BASELINE_COMMENT = (
    "Two different things, both of which a settings rename walks straight "
    "past. An 'unknown-field' entry is a name that is already wrong today -- "
    "a bug parked here so the gate can be green, not accepted debt. A "
    "'dynamic-name' entry is a name assembled at runtime, so no rename can be "
    "checked against it; that list is the checklist for splitting "
    "app/core/config.py. This file may shrink freely; growing it means one "
    "more field name nothing is watching. See scripts/check_settings_attrs.py."
)

# Import path -> exported singleton name. Each entry is a module-level
# `BaseSettings` instance that code reads configuration from.
SETTINGS_SOURCES = {
    "app.core.config": ("settings",),
    "app.core.infrastructure.events.config": ("event_transport_settings",),
    "app.modules.agent.config": ("agent_settings",),
    "app.modules.agent_surfaces.config": ("surface_settings",),
    "app.modules.apps.config": ("apps_settings",),
    "app.modules.connectors.config": ("connector_settings",),
    "app.modules.datastore.config": ("datastore_settings",),
    "app.modules.function.config": ("function_settings", "revision_settings"),
    "app.modules.icon.config": ("icon_settings",),
    "app.modules.identity.config": ("identity_settings",),
    "app.modules.pod_bundle.config": ("pod_bundle_settings",),
    "app.modules.schedule.config": ("schedule_settings",),
    "app.modules.usage.config": ("usage_settings",),
    "app.modules.workflow.config": ("workflow_settings",),
    "app.modules.workspace.config": ("workspace_settings",),
}

# `test_every_settings_singleton_is_checked` asserts this covers the tree. The
# list had fallen four behind while `app/core/config.py` was being split: every
# field that moved to `identity`, `usage`, `workflow` or `function` left the one
# gate that would have caught a stale reader, and nothing said so.

# The builtins. `monkeypatch.setattr` is deliberately absent -- see the blind
# spots above.
DYNAMIC_ACCESSORS = frozenset({"getattr", "setattr", "hasattr", "delattr"})

# Resolution follows a name to the expression that bound it, that expression to
# its container, and so on. The cap is a cycle breaker for `a = b; b = a`, not
# a tuning knob; the deepest real chain in the tree is six.
_MAX_DEPTH = 12


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    scope: str
    rule: str
    detail: str

    def key(self) -> str:
        """Identity for the baseline: no line number, so edits above don't churn."""
        return f"{self.path}::{self.scope}::{self.rule}::{self.detail}"

    def render(self) -> str:
        return f"{self.path}:{self.line}  {self.rule}  in {self.scope}  [{self.detail}]"


@dataclass(frozen=True)
class Binding:
    """How a local name got its value.

    ``element`` distinguishes ``name = expr`` from ``for name in expr``, and
    ``index`` carries the position in ``for name, value in expr.items()``.
    """

    expr: ast.expr
    element: bool = False
    index: int | None = None


@dataclass(frozen=True)
class Container:
    """A collection of string literals, seen from its two access shapes.

    ``iterated`` is what a ``for`` loop over it yields (a dict's keys, a
    sequence's items); ``indexed`` is what ``[...]`` or ``.get()`` returns.
    ``None`` on either side means the literals could not be established, which
    is not the same as an empty collection.
    """

    iterated: frozenset[str] | None = None
    indexed: frozenset[str] | None = None


@dataclass
class Frame:
    """The name bindings of one lexical scope."""

    bindings: dict[str, list[Binding]] = field(default_factory=dict)
    # `name.update({...})` -- a dict assembled in branches, which the workspace
    # e2e harness does for exactly the fields the original incident was about.
    grown: dict[str, list[ast.expr]] = field(default_factory=dict)

    def bind(self, name: str, binding: Binding) -> None:
        self.bindings.setdefault(name, []).append(binding)

    def grow(self, name: str, expr: ast.expr) -> None:
        self.grown.setdefault(name, []).append(expr)


def _known_attributes() -> dict[str, set[str]]:
    import importlib

    known: dict[str, set[str]] = {}
    for module_path, names in SETTINGS_SOURCES.items():
        module = importlib.import_module(module_path)
        for name in names:
            known[name] = set(dir(getattr(module, name)))
    return known


def _local_aliases(tree: ast.Module) -> dict[str, str]:
    """Map the name a file uses locally back to the canonical singleton.

    `from app.core.config import settings as app_settings` is common in tests,
    so the local name is not reliably the exported one.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        exported = SETTINGS_SOURCES.get(node.module)
        if not exported:
            continue
        for alias in node.names:
            if alias.name in exported:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _python_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and "alembic" not in path.parts
    )


def _own_nodes(statements: Iterable[ast.AST]) -> Iterator[ast.AST]:
    """Walk statements without descending into nested scopes.

    A name bound inside a nested ``def`` is not in force at the call site being
    resolved, and treating it as if it were would validate the wrong literals.
    """
    for statement in statements:
        if isinstance(
            statement,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            continue
        yield statement
        for child in ast.iter_child_nodes(statement):
            yield from _own_nodes([child])


def _bind_iteration(frame: Frame, target: ast.expr, iterable: ast.expr) -> None:
    if isinstance(target, ast.Name):
        frame.bind(target.id, Binding(iterable, element=True))
        return
    if isinstance(target, ast.Tuple):
        for index, element in enumerate(target.elts):
            if isinstance(element, ast.Name):
                frame.bind(element.id, Binding(iterable, element=True, index=index))


def _frame_for(statements: Sequence[ast.stmt]) -> Frame:
    """Assignments in force across a whole scope.

    Loop targets are deliberately not among them. Python leaks them past the
    loop, but a function with two ``for key, value in ...:`` loops over
    different dicts would then resolve ``key`` to the union of both -- and
    reading a workspace field name as an environment variable name is a
    confident wrong answer, which is worse than no answer.
    """
    frame = Frame()
    for node in _own_nodes(statements):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    frame.bind(target.id, Binding(node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                frame.bind(node.target.id, Binding(node.value))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
            and len(node.args) == 1
        ):
            frame.grow(node.func.value.id, node.args[0])
    return frame


def _frame_for_generators(generators: Sequence[ast.comprehension]) -> Frame:
    frame = Frame()
    for generator in generators:
        _bind_iteration(frame, generator.target, generator.iter)
    return frame


def _string_elements(nodes: Sequence[ast.expr | None]) -> frozenset[str] | None:
    """The literals of a collection, or ``None`` if any element is computed.

    Partial knowledge is worse than none here: validating three of five keys
    would report a clean run over a table whose other two are wrong.
    """
    found: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            return None
        found.add(node.value)
    return frozenset(found)


def _lookup(name: str, frames: Sequence[Frame]) -> list[Binding]:
    for frame in reversed(frames):
        bindings = frame.bindings.get(name)
        if bindings:
            return bindings
    return []


def _grown(name: str, frames: Sequence[Frame]) -> list[ast.expr]:
    return [expr for frame in frames for expr in frame.grown.get(name, ())]


def _merge(left: Container, right: Container) -> Container:
    def both(
        a: frozenset[str] | None, b: frozenset[str] | None
    ) -> frozenset[str] | None:
        return None if a is None or b is None else a | b

    return Container(
        both(left.iterated, right.iterated), both(left.indexed, right.indexed)
    )


def _container(expr: ast.expr, frames: Sequence[Frame], depth: int) -> Container:
    if depth > _MAX_DEPTH:
        return Container()
    if isinstance(expr, ast.Dict):
        return Container(_string_elements(expr.keys), _string_elements(expr.values))
    if isinstance(expr, (ast.List, ast.Tuple, ast.Set)):
        elements = _string_elements(expr.elts)
        return Container(elements, elements)
    if isinstance(expr, ast.DictComp):
        inner = [*frames, _frame_for_generators(expr.generators)]
        return Container(
            _resolve(expr.key, inner, depth + 1), _resolve(expr.value, inner, depth + 1)
        )
    if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        inner = [*frames, _frame_for_generators(expr.generators)]
        elements = _resolve(expr.elt, inner, depth + 1)
        return Container(elements, elements)
    if isinstance(expr, ast.Name):
        bindings = [b for b in _lookup(expr.id, frames) if not b.element]
        sources = [b.expr for b in bindings] + _grown(expr.id, frames)
        if not sources:
            return Container()
        merged = Container(frozenset(), frozenset())
        for source in sources:
            merged = _merge(merged, _container(source, frames, depth + 1))
        return merged
    return Container()


def _elements(
    binding: Binding, frames: Sequence[Frame], depth: int
) -> frozenset[str] | None:
    """The literals a ``for`` target can take, honouring ``.items()`` unpacking."""
    iterable = binding.expr
    if (
        isinstance(iterable, ast.Call)
        and isinstance(iterable.func, ast.Attribute)
        and iterable.func.attr in ("items", "keys", "values")
    ):
        container = _container(iterable.func.value, frames, depth + 1)
        if iterable.func.attr == "keys":
            return container.iterated
        if iterable.func.attr == "values":
            return container.indexed
        if binding.index == 0:
            return container.iterated
        if binding.index == 1:
            return container.indexed
        return None
    if binding.index is not None:
        # `for name, env in EXPECTED:` -- rows of a table, not a mapping.
        return None
    return _container(iterable, frames, depth + 1).iterated


def _resolve(
    expr: ast.expr, frames: Sequence[Frame], depth: int = 0
) -> frozenset[str] | None:
    """Every string the expression can be, or ``None`` when that is unknowable."""
    if depth > _MAX_DEPTH:
        return None
    if isinstance(expr, ast.Constant):
        return frozenset({expr.value}) if isinstance(expr.value, str) else None
    if isinstance(expr, ast.BoolOp):
        parts = [_resolve(value, frames, depth + 1) for value in expr.values]
    elif isinstance(expr, ast.IfExp):
        parts = [
            _resolve(expr.body, frames, depth + 1),
            _resolve(expr.orelse, frames, depth + 1),
        ]
    elif isinstance(expr, ast.Name):
        bindings = _lookup(expr.id, frames)
        if not bindings:
            return None
        parts = [
            _elements(binding, frames, depth + 1)
            if binding.element
            else _resolve(binding.expr, frames, depth + 1)
            for binding in bindings
        ]
    elif isinstance(expr, ast.Subscript):
        return _container(expr.value, frames, depth + 1).indexed
    elif (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr in ("get", "pop")
    ):
        return _container(expr.func.value, frames, depth + 1).indexed
    else:
        return None

    found: set[str] = set()
    for part in parts:
        if part is None:
            return None
        found |= part
    return frozenset(found)


class SettingsChecker(ast.NodeVisitor):
    def __init__(
        self, path: str, aliases: dict[str, str], known: dict[str, set[str]]
    ) -> None:
        self.path = path
        self.aliases = aliases
        self.known = known
        self.violations: list[Violation] = []
        self.dynamic_sites = 0
        self._frames: list[Frame] = []
        self._scope: list[str] = []

    def run(self, tree: ast.Module) -> None:
        self._frames.append(_frame_for(tree.body))
        self.generic_visit(tree)
        self._frames.pop()

    def _scope_name(self) -> str:
        return ".".join(self._scope) or "<module>"

    def _visit_scoped(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        self._scope.append(node.name)
        self._frames.append(_frame_for(node.body))
        self.generic_visit(node)
        self._frames.pop()
        self._scope.pop()

    visit_FunctionDef = _visit_scoped
    visit_AsyncFunctionDef = _visit_scoped
    visit_ClassDef = _visit_scoped

    def _visit_comprehension(
        self, node: ast.DictComp | ast.ListComp | ast.SetComp | ast.GeneratorExp
    ) -> None:
        self._frames.append(_frame_for_generators(node.generators))
        self.generic_visit(node)
        self._frames.pop()

    visit_DictComp = _visit_comprehension
    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def _visit_loop(self, node: ast.For | ast.AsyncFor) -> None:
        frame = Frame()
        _bind_iteration(frame, node.target, node.iter)
        self._frames.append(frame)
        self.generic_visit(node)
        self._frames.pop()

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            exported = self.aliases.get(node.value.id)
            if exported is not None and node.attr not in self.known[exported]:
                self._add(node.lineno, "unknown-field", f"{exported}.{node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._check_dynamic(node)
        self.generic_visit(node)

    def _check_dynamic(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Name):
            return
        if node.func.id not in DYNAMIC_ACCESSORS or len(node.args) < 2:
            return
        target = node.args[0]
        if not isinstance(target, ast.Name):
            return
        exported = self.aliases.get(target.id)
        if exported is None:
            return
        self.dynamic_sites += 1
        names = _resolve(node.args[1], self._frames)
        if names is None:
            self._add(
                node.lineno,
                "dynamic-name",
                f"{exported}.<{ast.unparse(node.args[1])}>",
            )
            return
        for name in sorted(names):
            if name not in self.known[exported]:
                self._add(node.lineno, "unknown-field", f"{exported}.{name}")

    def _add(self, line: int, rule: str, detail: str) -> None:
        self.violations.append(
            Violation(
                path=self.path,
                line=line,
                scope=self._scope_name(),
                rule=rule,
                detail=detail,
            )
        )


def collect() -> tuple[list[Violation], int]:
    known = _known_attributes()
    violations: list[Violation] = []
    dynamic_sites = 0
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError, UnicodeDecodeError:
            continue
        aliases = _local_aliases(tree)
        if not aliases:
            continue
        checker = SettingsChecker(str(path.relative_to(ROOT)), aliases, known)
        checker.run(tree)
        violations.extend(checker.violations)
        dynamic_sites += checker.dynamic_sites
    violations.sort(key=lambda item: (item.path, item.line, item.rule, item.detail))
    return violations, dynamic_sites


def _load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    entries = json.loads(path.read_text(encoding="utf-8"))["violations"]
    return {key: int(count) for key, count in entries.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check settings field names.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine.",
    )
    args = parser.parse_args()

    violations, dynamic_sites = collect()

    if args.update_baseline:
        payload = {
            "_comment": BASELINE_COMMENT,
            "violations": dict(
                sorted(Counter(item.key() for item in violations).items())
            ),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {sum(payload['violations'].values())} entries")
        return 0

    baseline = _load_baseline(args.baseline)
    counts = Counter(item.key() for item in violations)
    seen: Counter[str] = Counter()
    new: list[Violation] = []
    for violation in violations:
        seen[violation.key()] += 1
        if seen[violation.key()] > baseline.get(violation.key(), 0):
            new.append(violation)
    fixed = sum(
        max(0, allowed - counts.get(key, 0)) for key, allowed in baseline.items()
    )

    if fixed:
        print(f"✓ {fixed} baselined violation(s) gone — run --update-baseline")
    if not new:
        print(
            f"✓ settings attributes: no new violations "
            f"({sum(len(names) for names in SETTINGS_SOURCES.values())} objects, "
            f"{dynamic_sites} dynamic sites, "
            f"{sum(baseline.values())} baselined)"
        )
        # A baselined `dynamic-name` is a name nothing can check. A baselined
        # `unknown-field` is a name that is already wrong, and reprinting it on
        # every green run is the difference between recorded and forgotten.
        wrong = sorted(
            key for key in baseline if key.split("::", 3)[2] == "unknown-field"
        )
        for key in wrong:
            path, scope, _, detail = key.split("::", 3)
            print(f"  still wrong: {path}  {detail}  in {scope}")
        return 0

    print(f"✗ settings attributes: {len(new)} new violation(s)\n")
    for violation in new:
        print(f"  {violation.render()}")
    print(
        "\nName a field that exists. If the name is computed, either build it "
        "from a table of literals in the same module or accept that no rename "
        "can be checked against it and baseline the site."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
