#!/usr/bin/env python3
"""Fail the build on a new memory-retention hazard in ``app/``.

The API process grew without bound in production, and no single change was the
cause: it was the sum of small shapes that each retain memory for the life of
the process. Each rule below names one of them. They are ratcheted, not banned:
the existing ones live in ``memory-hazards-baseline.json`` and may only shrink.

``MH001`` engine without ``query_cache_size``
    SQLAlchemy keeps a compiled-statement LRU per engine (default 500 entries);
    with dynamic SQL each entry is a distinct compiled object. Say how big.

``MH002`` unbounded or per-instance ``functools`` cache
    ``@cache`` / ``lru_cache(maxsize=None)`` grow forever, and ``lru_cache`` on
    a method keys on ``self`` and so pins every instance it ever saw.

``MH003`` module/class-level mutable container mutated from a function
    A dict/list/set at module or class level that a function writes into is a
    process-lifetime cache with no eviction. Use ``BoundedDict``/``BoundedSet``
    (``app.core.bounded``), a weakref container, or mark the assignment
    ``# memory: bounded <reason>`` when the key space is genuinely finite.

``MH004`` httpx client outside ``with``
    Each client owns a connection pool. One made per call and never closed
    leaks it; a shared one is built by an ``lru_cache``d factory, lives in
    ``app/core/net/``, or is marked ``# memory: shared client``.

``MH005`` whole-body buffering
    ``await request.body()`` / ``await upload.read()`` with no size reads the
    entire payload into memory. Stream it, or mark ``# memory: bounded <reason>``.

``MH006`` tracing without limits
    ``BatchSpanProcessor`` without ``max_queue_size`` and ``TracerProvider``
    without ``span_limits`` rely on library defaults that were the retention.

``MH007`` fire-and-forget task
    A discarded ``create_task``/``ensure_future`` result is only weakly held by
    the loop: it can be collected mid-flight, and nothing bounds how many pile
    up. Keep the reference (a task set, a TaskGroup) or await it.

Findings are keyed by file, enclosing symbol, rule and a short detail -- never
the line number -- so edits elsewhere in a file do not churn the baseline.

Usage::

    uv run python scripts/check_memory_hazards.py
    uv run python scripts/check_memory_hazards.py --report
    uv run python scripts/check_memory_hazards.py --update-baseline
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "memory-hazards-baseline.json"
SCAN_ROOT = ROOT / "app"
EXCLUDED_PARTS = ("tests", "test_support")
SHARED_CLIENT_DIR = "app/core/net/"

RULES = {
    "MH001": "engine without query_cache_size",
    "MH002": "unbounded or per-instance functools cache",
    "MH003": "module/class-level mutable container mutated at runtime",
    "MH004": "httpx client constructed outside with",
    "MH005": "whole-body buffering",
    "MH006": "tracing without queue/span limits",
    "MH007": "fire-and-forget task",
}

ENGINE_FACTORIES = {"create_async_engine", "create_engine"}
MUTABLE_FACTORIES = {"dict", "set", "list", "OrderedDict", "defaultdict", "Counter"}
BOUNDED_FACTORIES = {
    "BoundedDict",
    "BoundedSet",
    "WeakValueDictionary",
    "WeakKeyDictionary",
    "WeakSet",
}
MUTATING_METHODS = {
    "setdefault",
    "add",
    "append",
    "appendleft",
    "update",
    "extend",
    "insert",
    "__setitem__",
}
HTTPX_CLIENTS = {"AsyncClient", "Client"}
TASK_SPAWNERS = {"create_task", "ensure_future"}
BOUNDED_MARK = "# memory: bounded"
SHARED_CLIENT_MARK = "# memory: shared client"


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
        return (
            f"{self.path}:{self.line}  {self.rule}  in {self.scope}()  [{self.detail}]"
        )


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _has_kw(call: ast.Call, name: str) -> bool:
    # `**kwargs` may carry it; we cannot tell, so do not accuse.
    return any(kw.arg == name or kw.arg is None for kw in call.keywords)


def _decorator_hazard(decorator: ast.expr, is_method: bool) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = _callee(target)
    if name == "cache":
        return "cache"
    if name != "lru_cache":
        return None
    if isinstance(decorator, ast.Call):
        for kw in decorator.keywords:
            if (
                kw.arg == "maxsize"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is None
            ):
                return "lru_cache(maxsize=None)"
        if (
            decorator.args
            and isinstance(decorator.args[0], ast.Constant)
            and decorator.args[0].value is None
        ):
            return "lru_cache(maxsize=None)"
    return "method-lru_cache" if is_method else None


def _mutable_value(value: ast.expr | None) -> bool:
    if isinstance(value, (ast.Dict, ast.Set, ast.List)):
        return True
    if isinstance(value, ast.Call):
        name = _callee(value.func)
        if name in BOUNDED_FACTORIES:
            return False
        if name == "deque":
            return not _has_kw(value, "maxlen") and len(value.args) < 2
        return name in MUTABLE_FACTORIES
    return False


def _local_names(func: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound locally in ``func`` and names it declares global."""
    bound: set[str] = set()
    declared: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            declared.update(node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return bound, declared


def _mutated_base(node: ast.AST) -> ast.expr | None:
    """The container expression that ``node`` writes into, if it writes.

    ``C[k].append(v)`` counts as a write to ``C``: a ``defaultdict`` inserts on
    the read, and a nested container grows its parent either way.
    """
    base = _direct_mutated_base(node)
    while isinstance(base, ast.Subscript):
        base = base.value
    return base


def _direct_mutated_base(node: ast.AST) -> ast.expr | None:
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript):
                return target.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in MUTATING_METHODS
    ):
        return node.func.value
    return None


class MemoryHazardChecker(ast.NodeVisitor):
    def __init__(self, path: str, source: str = "") -> None:
        self.path = path
        self.lines = source.splitlines()
        self.violations: list[Violation] = []
        self._scope: list[str] = []
        self._in_class: list[bool] = []
        self._with_items: set[int] = set()
        self._upload_params: list[set[str]] = []
        # Per enclosing function: is it a memoized (``lru_cache``/``cache``)
        # factory? A client built there is a process singleton, which is the
        # shape ``check_io_hygiene`` demands, not a per-call leak.
        self._memoized: list[bool] = []

    # --- plumbing --------------------------------------------------------

    def _marked(self, node: ast.AST, mark: str) -> bool:
        start = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", start) or start
        return any(
            mark in self.lines[i - 1]
            for i in range(start, end + 1)
            if 0 < i <= len(self.lines)
        )

    def _scope_name(self) -> str:
        return ".".join(self._scope) or "<module>"

    def _add(
        self, node: ast.AST, rule: str, detail: str, scope: str | None = None
    ) -> None:
        self.violations.append(
            Violation(
                path=self.path,
                line=getattr(node, "lineno", 0),
                scope=scope or self._scope_name(),
                rule=rule,
                detail=detail,
            )
        )

    def check(self, tree: ast.Module) -> list[Violation]:
        self._check_containers(tree)
        self.visit(tree)
        return self.violations

    # --- scopes ----------------------------------------------------------

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = bool(self._in_class and self._in_class[-1])
        first = node.args.posonlyargs + node.args.args
        is_method = is_method and bool(first) and first[0].arg in ("self", "cls")
        for decorator in node.decorator_list:
            hazard = _decorator_hazard(decorator, is_method)
            if hazard:
                self._add(
                    decorator,
                    "MH002",
                    hazard,
                    scope=".".join([*self._scope, node.name]),
                )
        uploads = {
            arg.arg
            for arg in [*first, *node.args.kwonlyargs]
            if arg.annotation is not None
            and "UploadFile" in ast.unparse(arg.annotation)
        }
        self._scope.append(node.name)
        self._in_class.append(False)
        self._upload_params.append(uploads)
        self._memoized.append(
            any(
                _callee(d.func if isinstance(d, ast.Call) else d)
                in ("lru_cache", "cache")
                for d in node.decorator_list
            )
        )
        self.generic_visit(node)
        self._memoized.pop()
        self._upload_params.pop()
        self._in_class.pop()
        self._scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self._in_class.append(True)
        self.generic_visit(node)
        self._in_class.pop()
        self._scope.pop()

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self._with_items.add(id(item.context_expr))
        self.generic_visit(node)

    visit_With = _visit_with
    visit_AsyncWith = _visit_with

    # --- call-shaped rules ----------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee(node.func)
        if name in ENGINE_FACTORIES and not _has_kw(node, "query_cache_size"):
            self._add(node, "MH001", name)
        elif name == "BatchSpanProcessor" and not _has_kw(node, "max_queue_size"):
            self._add(node, "MH006", "BatchSpanProcessor")
        elif name == "TracerProvider" and not _has_kw(node, "span_limits"):
            self._add(node, "MH006", "TracerProvider")
        elif name in HTTPX_CLIENTS and self._is_httpx(node.func):
            if not (
                id(node) in self._with_items
                or (self._memoized and self._memoized[-1])
                or self.path.startswith(SHARED_CLIENT_DIR)
                or self._marked(node, SHARED_CLIENT_MARK)
            ):
                self._add(node, "MH004", f"httpx.{name}")
        self.generic_visit(node)

    def _is_httpx(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Attribute):
            return isinstance(func.value, ast.Name) and func.value.id == "httpx"
        return isinstance(func, ast.Name) and func.id in self._httpx_imports

    _httpx_imports: set[str] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self._httpx_imports = {
            alias.asname or alias.name
            for stmt in ast.walk(node)
            if isinstance(stmt, ast.ImportFrom) and stmt.module == "httpx"
            for alias in stmt.names
            if alias.name in HTTPX_CLIENTS
        }
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and not call.args
            and not call.keywords
            and isinstance(call.func.value, ast.Name)
            and not self._marked(node, BOUNDED_MARK)
        ):
            receiver = call.func.value.id
            method = call.func.attr
            uploads = self._upload_params[-1] if self._upload_params else set()
            if method == "body" and receiver == "request":
                self._add(node, "MH005", "request.body()")
            elif method == "read" and receiver in uploads:
                self._add(node, "MH005", f"{receiver}.read()")
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        call = node.value
        if isinstance(call, ast.Call) and _callee(call.func) in TASK_SPAWNERS:
            func = call.func
            # TaskGroup.create_task keeps its own reference; only the loop does not.
            receiver = func.value if isinstance(func, ast.Attribute) else None
            if (
                receiver is None
                or (
                    isinstance(receiver, ast.Name)
                    and (receiver.id == "asyncio" or "loop" in receiver.id.lower())
                )
                or (isinstance(receiver, ast.Call) and "loop" in ast.unparse(receiver))
            ):
                target = ""
                if call.args and isinstance(call.args[0], ast.Call):
                    target = _callee(call.args[0].func) or ""
                self._add(node, "MH007", f"{ast.unparse(func)}({target})")
        self.generic_visit(node)

    # --- MH003 ----------------------------------------------------------

    def _check_containers(self, tree: ast.Module) -> None:
        module_level: dict[str, ast.stmt] = {}
        for stmt in tree.body:
            name = self._container_binding(stmt)
            if name:
                module_level[name] = stmt
        functions = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for name, stmt in module_level.items():
            if self._mutated_anywhere(functions, name):
                self._add(stmt, "MH003", name, scope="<module>")

        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for stmt in cls.body:
                name = self._container_binding(stmt)
                if name and self._class_attr_mutated(cls, name):
                    self._add(stmt, "MH003", name, scope=cls.name)

    def _container_binding(self, stmt: ast.stmt) -> str | None:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target, value = stmt.target, stmt.value
            if "ClassVar" not in ast.unparse(stmt.annotation) and not isinstance(
                stmt.target, ast.Name
            ):
                return None
        else:
            return None
        if not isinstance(target, ast.Name) or not _mutable_value(value):
            return None
        if self._marked(stmt, BOUNDED_MARK):
            return None
        return target.id

    def _mutated_anywhere(self, functions: list[ast.AST], name: str) -> bool:
        for func in functions:
            bound, declared = _local_names(func)
            if name in bound and name not in declared:
                continue
            for node in ast.walk(func):
                base = _mutated_base(node)
                if isinstance(base, ast.Name) and base.id == name:
                    return True
        return False

    def _class_attr_mutated(self, cls: ast.ClassDef, name: str) -> bool:
        owners = {"self", "cls", cls.name}
        # An instance that rebinds the attribute shadows the class-level one.
        for node in ast.walk(cls):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else ([node.target] if isinstance(node, ast.AnnAssign) else [])
            )
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == name
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    return False
        for node in ast.walk(cls):
            base = _mutated_base(node)
            if (
                isinstance(base, ast.Attribute)
                and base.attr == name
                and isinstance(base.value, ast.Name)
                and base.value.id in owners
            ):
                return True
        return False


def collect(paths: list[Path]) -> list[Violation]:
    found: list[Violation] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        checker = MemoryHazardChecker(str(path.relative_to(ROOT)), source)
        found.extend(checker.check(tree))
    return sorted(found, key=lambda v: (v.path, v.line, v.rule))


def source_files() -> list[Path]:
    return sorted(
        path
        for path in SCAN_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def _load_baseline(path: Path) -> dict[str, int]:
    entries = json.loads(path.read_text(encoding="utf-8"))["violations"]
    return {key: int(count) for key, count in entries.items()}


def report(baseline: dict[str, int], top: int = 10) -> None:
    """Show the existing debt: per-rule totals and the files carrying most of it."""
    by_rule: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    for key, count in baseline.items():
        path, _scope, rule, _detail = key.split("::", 3)
        by_rule[rule] += count
        by_file[path] += count
    print(f"memory hazards baselined: {sum(baseline.values())}")
    for rule in sorted(RULES):
        print(f"  {rule}  {by_rule.get(rule, 0):4d}  {RULES[rule]}")
    print(f"top {top} files:")
    for path, count in by_file.most_common(top):
        print(f"  {count:4d}  {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print per-rule and per-file baseline debt.",
    )
    args = parser.parse_args()

    violations = collect(source_files())

    if args.update_baseline:
        payload = {
            "_comment": (
                "Pre-existing memory-retention hazards in app/. This file may "
                "shrink freely; growing it means a new process-lifetime "
                "retention path. See scripts/check_memory_hazards.py."
            ),
            "violations": dict(sorted(Counter(v.key() for v in violations).items())),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {sum(payload['violations'].values())} entries")
        report(payload["violations"])
        return 0

    baseline = _load_baseline(args.baseline)
    if args.report:
        report(baseline)

    counts = Counter(v.key() for v in violations)
    new: list[Violation] = []
    seen: Counter[str] = Counter()
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
            f"✓ memory hazards: no new violations ({sum(baseline.values())} baselined)"
        )
        return 0

    print(f"✗ memory hazards: {len(new)} new violation(s)\n")
    for violation in new:
        print(f"  {violation.render()}  {RULES[violation.rule]}")
    print(
        "\nBound it: BoundedDict/BoundedSet from app.core.bounded, an explicit "
        "size, a `with` block, or a kept task reference. A genuinely finite case "
        "takes `# memory: bounded <reason>` on the line."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
