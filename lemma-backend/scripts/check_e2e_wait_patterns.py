#!/usr/bin/env python3
"""Fail the build on a new hand-rolled poll loop in the e2e suite.

``app/modules/test_support/e2e/waiters.py`` (``eventually()`` / ``wait_for_status()``)
is the one canonical way an e2e test waits for async state to settle: a bounded
loop with a real timeout, a real interval, and a real error message on failure.
Before it was adopted throughout the suite, the same shape --
``for``/``while`` ... ``await asyncio.sleep(...)`` -- was hand-rolled roughly
fifty-five times across forty-two files, at inconsistent intervals (1.0s down to
0.05s), several with bugs a shared, tested helper does not have (a soft timeout
that returns stale data instead of failing, a truthy check that silently
disabled an explicit empty set). Migrating all of them is only worth it if the
pattern cannot quietly grow back a fifty-sixth way.

``hand-rolled-poll-loop``
    A ``for`` or ``while`` loop whose body calls ``asyncio.sleep``,
    ``anyio.sleep``, or ``time.sleep`` -- resolved through the file's own
    import aliases, the same way ``check_io_hygiene.py`` tells ``httpx.AsyncClient``
    apart from an unrelated ``AsyncClient``. Only the outermost such loop is
    reported: a poll loop's own retry-until-give-up shape often nests an
    ``if``/``try`` that would otherwise register as a second, redundant
    violation for the same wait.

This is deliberately dumb about *why* a loop exists -- it does not know a
pytest marker from a docstring. A loop that is a genuine, permanent exception
(a real external OAuth wait, a fake hang used to prove a deadline fires) is not
special-cased in the checker; it is named once in the baseline, with a comment
explaining why, exactly like its siblings. New code gets no such exemption for
free.

Ratcheted against a baseline: it may shrink freely, and anything new fails the
build. See ``scripts/check_io_hygiene.py`` and ``scripts/check_session_scope.py``
for the sibling gates this one is modeled on.

Usage::

    uv run python scripts/check_e2e_wait_patterns.py
    uv run python scripts/check_e2e_wait_patterns.py --update-baseline
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
DEFAULT_BASELINE = ROOT / "e2e-wait-patterns-baseline.json"

SCAN_ROOT = ROOT / "app"

# Fully-qualified sleep primitives. `time.sleep` is included deliberately: a
# handful of e2e helpers poll a *synchronous* external API (Composio OAuth) and
# hand-roll their retry loop in blocking code, not a coroutine -- the same
# offence, just without an `await`.
SLEEP_CALLS = {"asyncio.sleep", "anyio.sleep", "time.sleep"}


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
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


class _ImportAliases(ast.NodeVisitor):
    """Map the names a module actually uses onto their fully-qualified originals.

    ``from asyncio import sleep`` and ``from time import sleep`` put the same
    bare identifier in two files meaning two different waits; reading the
    imports is what lets the rule below tell them apart.
    """

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module:
            return  # relative import: not one of the modules this rule names
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def resolve(self, dotted: str) -> str:
        if not dotted:
            return dotted
        head, _, rest = dotted.partition(".")
        target = self.aliases.get(head)
        if target is None:
            return dotted
        return f"{target}.{rest}" if rest else target


class _SleepFinder(ast.NodeVisitor):
    """Whether a loop's own body calls a sleep primitive, stopping at any
    boundary that means the call no longer belongs to *this* loop's wait."""

    def __init__(self, aliases: _ImportAliases) -> None:
        self._aliases = aliases
        self.found: ast.Call | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # a helper defined inside the loop is not part of this loop's wait

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Call(self, node: ast.Call) -> None:
        if self.found is not None:
            return
        if self._aliases.resolve(_dotted(node.func)) in SLEEP_CALLS:
            self.found = node
            return
        self.generic_visit(node)


class WaitPatternChecker(ast.NodeVisitor):
    def __init__(self, path: str, aliases: _ImportAliases) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._scope: list[str] = []
        self._aliases = aliases

    def _visit_scoped(self, node: ast.AST, name: str) -> None:
        self._scope.append(name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node, node.name)

    def _maybe_flag_loop(self, node: ast.For | ast.While) -> None:
        finder = _SleepFinder(self._aliases)
        finder.visit(node)
        if finder.found is not None:
            self._record(node.lineno, self._aliases.resolve(_dotted(finder.found.func)))
            # Don't descend: a nested loop inside an already-flagged poll loop
            # is almost always part of the same wait, not a second one.
            return
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._maybe_flag_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._maybe_flag_loop(node)

    def _record(self, line: int, detail: str) -> None:
        scope = ".".join(self._scope) or "<module>"
        self.violations.append(
            Violation(self.path, line, scope, "hand-rolled-poll-loop", detail)
        )


def collect(paths: list[Path]) -> list[Violation]:
    found: list[Violation] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases = _ImportAliases()
        aliases.visit(tree)
        checker = WaitPatternChecker(str(path.relative_to(ROOT)), aliases)
        checker.visit(tree)
        found.extend(checker.violations)
    return sorted(found, key=lambda v: (v.path, v.line, v.rule))


def source_files() -> list[Path]:
    """Every e2e test file -- anywhere a `tests/e2e` path segment appears, not
    just one level under a module, since core's event-quarantine e2e tests live
    a directory deeper (`app/core/infrastructure/events/tests/e2e`).

    ``waiters.py`` itself lives in ``test_support/e2e/`` -- no ``tests``
    segment -- so it is outside this glob without needing an explicit
    exemption; it is the one place this shape is the implementation, not a
    violation of it.
    """
    return sorted(
        path
        for path in SCAN_ROOT.rglob("*.py")
        if "tests" in path.parts and "e2e" in path.parts
    )


def _load_baseline(path: Path) -> dict[str, int]:
    entries = json.loads(path.read_text(encoding="utf-8"))["violations"]
    if isinstance(entries, dict):
        return {key: int(count) for key, count in entries.items()}
    return dict(Counter(entries))


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
                "Pre-existing hand-rolled e2e poll loops that do not go through "
                "waiters.eventually()/wait_for_status(). Each entry here is a "
                "deliberate, permanent exception (documented at its call site) -- "
                "not a backlog. This file may shrink freely; growing it means a "
                "new poll loop was hand-rolled instead of using the shared helper. "
                "See scripts/check_e2e_wait_patterns.py."
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
        print(
            f"✓ e2e wait patterns: no new hand-rolled poll loops ({sum(baseline.values())} baselined)"
        )
        return 0

    print(f"✗ e2e wait patterns: {len(new)} new hand-rolled poll loop(s)\n")
    for violation in new:
        print(f"  {violation.render()}")
    print(
        "\nUse app.modules.test_support.e2e.waiters.eventually() or wait_for_status() "
        "instead of hand-rolling a retry loop. If this really is a deliberate, "
        "permanent exception, say why at the call site and run --update-baseline."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
