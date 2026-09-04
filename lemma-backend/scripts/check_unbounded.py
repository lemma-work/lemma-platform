#!/usr/bin/env python3
"""Fail the build on a resource acquired without a bound.

Three rules, all from one production incident. A pod stored a document in a
table column; the row was copied whole into a Redis stream capped by entry
count rather than bytes; Redis was OOM-killed and login went down; and the API
event loop stalled for five seconds decoding the same entries, which its
liveness probe correctly read as a wedged process. One shape -- something
acquired with no ceiling -- in four different places.

``unbounded-queue``
    ``asyncio.Queue()`` with no ``maxsize``.

    A queue with no bound is not a buffer, it is a memory leak with a producer
    attached. The one on the model-streaming path held every token and tool
    result whenever an SSE consumer was slower than the model, with nothing
    pushing back. ``channel_service`` gets this right and is the pattern to
    copy: a bounded queue turns a slow consumer into backpressure.

``unbounded-cache``
    ``@lru_cache`` / ``@cache`` with no ``maxsize`` on a function that takes
    arguments.

    Argument-free memoization is a singleton and fine. Once there are
    parameters the cache is keyed by whatever callers pass, which on a
    long-lived API pod means per-organization, per-pod or per-user growth that
    nothing ever evicts.

``cpu-on-loop``
    Work known to be slow, called directly inside ``async def``.

    ``make lint-async`` covers blocking *I/O* and nothing else. It was green
    while production logged 461 loop stalls in a week, because the calls that
    stalled it were CPU: JSON over multi-megabyte payloads, ``importlib``
    walking the filesystem for a generated connector client (1.4s for one), and
    image decoding. Hand these to ``run_blocking`` so they run on a bounded
    thread pool instead of the loop.

All three are ratcheted against a baseline: it may shrink freely, and anything
new fails the build. See ``scripts/check_io_hygiene.py`` for the sibling gate on
I/O, and ``scripts/check_memory_bounds`` does not exist -- this is that gate.

Usage::

    uv run python scripts/check_unbounded.py
    uv run python scripts/check_unbounded.py --update-baseline
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
DEFAULT_BASELINE = ROOT / "unbounded-baseline.json"

SCAN_ROOT = ROOT / "app"
EXCLUDED_PARTS = ("tests", "test_support")

#: The module that owns the offload primitive cannot offload through itself.
OFFLOAD_OWNER = "app/core/concurrency"

#: Calls measured stalling a production event loop. Deliberately short: a rule
#: that flags everything expensive-looking is one people baseline wholesale.
CPU_CALLS = {
    "importlib.import_module",
    "PIL.Image.open",
    "Image.open",
}

#: Same, but only worth flagging where the input is not obviously small. These
#: are matched on the call name alone, so they carry the highest false-positive
#: risk and are the ones to revisit if the baseline stops being read.
CPU_CALLS_WIDE = {
    "json.loads",
    "json.dumps",
}


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


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._scope: list[str] = []
        self._async_depth = 0

    @property
    def scope(self) -> str:
        return ".".join(self._scope) or "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_cache(node)
        self._scope.append(node.name)
        # A plain `def` nested in a coroutine still runs on the loop, but a
        # top-level one is a helper whose callers this gate cannot see. That
        # gap is `check_io_hygiene`'s `sync-helper` territory, not this rule's.
        self.generic_visit(node)
        self._scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_cache(node)
        self._scope.append(node.name)
        self._async_depth += 1
        self.generic_visit(node)
        self._async_depth -= 1
        self._scope.pop()

    def _check_cache(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        takes_arguments = bool(
            node.args.args or node.args.posonlyargs or node.args.kwonlyargs
        )
        if not takes_arguments:
            return
        for decorator in node.decorator_list:
            name = _dotted(
                decorator.func if isinstance(decorator, ast.Call) else decorator
            )
            if not name.endswith(("lru_cache", "cache")):
                continue
            if name.endswith("cached_property"):
                continue
            bounded = isinstance(decorator, ast.Call) and any(
                keyword.arg == "maxsize" and not _is_none(keyword.value)
                for keyword in decorator.keywords
            )
            if not bounded:
                self.violations.append(
                    Violation(
                        self.path,
                        node.lineno,
                        self.scope + "." + node.name,
                        "unbounded-cache",
                        name,
                    )
                )

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        if name.endswith("asyncio.Queue") or name == "Queue":
            if not any(keyword.arg == "maxsize" for keyword in node.keywords):
                self.violations.append(
                    Violation(
                        self.path, node.lineno, self.scope, "unbounded-queue", name
                    )
                )
        if self._async_depth and name in (CPU_CALLS | CPU_CALLS_WIDE):
            if OFFLOAD_OWNER not in self.path:
                self.violations.append(
                    Violation(self.path, node.lineno, self.scope, "cpu-on-loop", name)
                )
        self.generic_visit(node)


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def source_files() -> list[Path]:
    return sorted(
        path
        for path in SCAN_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def collect(paths: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        visitor = _Visitor(str(path.relative_to(ROOT)))
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def _load_baseline(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("violations", {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current tree. Shrinking is always fine.",
    )
    args = parser.parse_args()

    violations = collect(source_files())

    if args.update_baseline:
        payload = {
            "_comment": (
                "Pre-existing unbounded queues and caches, and CPU-heavy calls "
                "made on the event loop. This file may shrink freely; growing it "
                "means a new resource acquired without a bound. See "
                "scripts/check_unbounded.py."
            ),
            "violations": dict(sorted(Counter(v.key() for v in violations).items())),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {sum(payload['violations'].values())} entries")
        return 0

    baseline = _load_baseline(args.baseline)
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
        print(f"✓ unbounded: no new violations ({sum(baseline.values())} baselined)")
        return 0

    print(f"✗ unbounded: {len(new)} new violation(s)\n")
    for violation in new:
        print(f"  {violation.render()}")
    print(
        "\nGive the queue a maxsize, the cache a maxsize, or hand the work to "
        "run_blocking(..., limiter=...) so it runs off the event loop."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
