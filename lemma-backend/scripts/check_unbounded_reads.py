#!/usr/bin/env python3
"""Fail the build on a query that reads a collection with no ceiling.

The sibling gates bound what a process *holds* -- a queue, a cache, the event
loop. This one bounds what it *asks for*.

An audit of the whole backend found twenty-five reads whose cost grew with how
much data a tenant had, when it did not need to. They were not twenty-five
mistakes: they were seven shapes, and every one of them is the same sentence in
SQL. Ask for everything belonging to a pod, an app, a function, a conversation
-- then keep a page of it, or take a `len()`, or filter it in Python down to the
handful the caller wanted. The result was always right, which is exactly why
none of them were noticed: the directory tree read three thousand files to show
seventy-two and rendered them perfectly.

``unbounded-read``
    A function that builds a ``select(...)``, materializes it as a list, and
    contains no ``limit``, no ``in_()``, and no aggregate.

    Those three are the ways a read is already bounded. ``limit`` is a page.
    ``in_()`` is a set of ids the caller already holds, so the result is as
    bounded as that set. An aggregate returns one row however many it read, and
    is usually the fix rather than the defect -- ``count()`` instead of
    ``len(list_everything())``.

    A single-row read is not a collection: ``scalar_one``, ``first()``,
    ``one_or_none()`` and friends are recognised and skipped.

``unbounded-statement``
    A function annotated ``-> Select`` that builds one with no ``limit``, no
    ``in_()`` and no aggregate.

    This rule exists because the first one has a hole, and the hole is the shape
    this codebase is moving towards. Repositories here put their reasoning in a
    statement builder beside them -- ``file_tree_sql``, ``surface_routing_sql``,
    ``file_listing_sql`` -- and the method that runs one is then three lines with
    no ``select`` in it at all. Split that way, an unbounded read is invisible
    to a rule that looks at one function: neither half has both the statement
    and the ``.all()``.

    So the builder is judged on its own terms. It is the statement; whether
    somebody else calls ``.all()`` on it does not change what it asks the
    database for.

What this rule cannot see, and does not pretend to: whether the thing being
read is *small*. Plenty of baselined entries below are reads of a bounded set
that happens to have no `LIMIT` -- one pod's surfaces, an org's connectors.
That is why this is a ratchet rather than a verifier. It is not asking "is this
wrong", it is asking "did somebody add another one", and the answer to the
second question is worth having even when the first is often no.

Baselined against the current tree: it may shrink freely, and anything new
fails the build. Bound the read, or record it here with the reason in the pull
request.

Usage::

    uv run python scripts/check_unbounded_reads.py
    uv run python scripts/check_unbounded_reads.py --update-baseline
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
DEFAULT_BASELINE = ROOT / "unbounded-reads-baseline.json"

SCAN_ROOT = ROOT / "app"
EXCLUDED_PARTS = ("tests", "test_support")

#: Ways a statement is already bounded. Any one of them and the read is not
#: this rule's business.
BOUNDING_CALLS = frozenset({"limit", "in_", "fetch"})

#: Aggregates return one row however many they read. Several of the fixes this
#: gate exists to protect *are* one of these.
AGGREGATES = frozenset({"count", "max", "min", "sum", "avg", "exists"})

#: Ways of taking a single row, which is not a collection.
SINGLE_ROW = frozenset(
    {
        "scalar",
        "scalar_one",
        "scalar_one_or_none",
        "one",
        "one_or_none",
        "first",
    }
)

#: Ways of materializing many rows. Without one of these the statement is being
#: built, not run -- a `Select` handed to somebody else, which is where the
#: bound often belongs and where this rule would only be guessing.
COLLECTING = frozenset({"all", "fetchall", "scalars", "partitions"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    scope: str
    rule: str

    def key(self) -> str:
        """Identity for the baseline: no line number, so edits above don't churn."""
        return f"{self.path}::{self.scope}::{self.rule}"

    def render(self) -> str:
        return f"{self.path}:{self.line}  {self.rule}  in {self.scope}()"


def _returns_select(node) -> bool:
    """Whether the annotation names a ``Select``, at any depth.

    ``-> Select`` and ``-> tuple[Select, Select]`` both count: the second is
    ``tree_statements``, which returns two and would otherwise be the one
    builder this rule could not see.
    """
    annotation = getattr(node, "returns", None)
    if annotation is None:
        return False
    return any(
        isinstance(child, ast.Name) and child.id in {"Select", "Update", "Delete"}
        for child in ast.walk(annotation)
    )


def _called_names(node: ast.AST) -> Counter[str]:
    """Every attribute or function name called under *node*, with counts.

    Counted rather than collected because one number decides whether the
    exemptions below mean anything: with a single ``select`` in a function, a
    ``limit`` or a ``count`` in it is about that statement. With two, it is
    about one of them, and the other is exactly what this rule is looking for --
    which is how the run-history loader hid, pairing an unbounded read of every
    run with a ``count`` over their messages.
    """
    names: Counter[str] = Counter()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute):
            names[func.attr] += 1
        elif isinstance(func, ast.Name):
            names[func.id] += 1
    return names


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._scope: list[str] = []

    @property
    def scope(self) -> str:
        return ".".join(self._scope) or "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_function(self, node) -> None:
        self._scope.append(node.name)
        self._check(node)
        self.generic_visit(node)
        self._scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _check(self, node) -> None:
        called = _called_names(node)
        if not called["select"]:
            return
        # An exemption only speaks for a read when there is one read for it to
        # speak for. A function building several statements gets no free pass
        # from one of them being bounded -- which is how the run-history loader
        # hid, pairing an unbounded read of every run in a conversation with a
        # `count` over their messages and a `scalar_one_or_none` for its id.
        if called["select"] == 1 and set(called) & (BOUNDING_CALLS | AGGREGATES):
            return
        if _returns_select(node):
            self.violations.append(
                Violation(
                    path=self.path,
                    line=node.lineno,
                    scope=self.scope,
                    rule="unbounded-statement",
                )
            )
            return
        if not set(called) & COLLECTING:
            return
        # A single-row read that also happens to call `.all()` somewhere else is
        # rare enough to be worth the false negative; the reverse is not.
        if set(called) & SINGLE_ROW and not (set(called) & {"all", "fetchall"}):
            return
        self.violations.append(
            Violation(
                path=self.path,
                line=node.lineno,
                scope=self.scope,
                rule="unbounded-read",
            )
        )


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
                "Collection reads with no limit, no in_() and no aggregate. This "
                "file may shrink freely; growing it means another query whose "
                "cost follows a tenant's data rather than the caller's request. "
                "See scripts/check_unbounded_reads.py."
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
        print(f"✓ {fixed} baselined read(s) bounded — run --update-baseline")
    if not new:
        print(
            f"✓ unbounded reads: no new violations ({sum(baseline.values())} baselined)"
        )
        return 0

    print(f"✗ unbounded reads: {len(new)} new unbounded collection read(s)\n")
    for violation in new:
        print(f"  {violation.render()}")
    print(
        "\nGive the query a `limit` and a cursor, narrow it with `in_()` over ids "
        "the caller already holds, or ask for the aggregate you actually wanted. "
        "If the set really is bounded by something this cannot see, record it "
        "with --update-baseline and say why in the pull request."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
