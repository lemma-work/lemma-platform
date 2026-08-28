#!/usr/bin/env python3
"""Fail the build on a broad ``except`` that leaves no trace of what it caught.

The incident: conversation titles "worked half the time". The cause was two
``except Exception:`` blocks that logged at ``logger.debug`` with no
``exc_info``. Production runs at ``LOG_LEVEL=INFO`` behind
``structlog.make_filtering_bound_logger``, so those records were dropped before
formatting -- and because the handler also stopped the exception from
propagating, the job reported ``outcome="succeeded"`` either way. Over seven
days: 2,146 title jobs, every one "successful", zero log lines, and a provider
outage that was indistinguishable from a healthy system. The failure was not
that titling broke; it was that nothing could say so.

Three rules, each about a handler that catches broadly and then says nothing.

``silent-broad-catch``
    A broad handler around awaited work that logs nothing at all. The failure
    becomes a return value -- ``None``, ``False``, ``[]`` -- indistinguishable
    from the legitimate empty answer. This is how an auth-backend outage reads
    as "invalid token" and a dead database reads as "no schedules configured".

``debug-only-broad-catch``
    A broad handler whose only record is ``logger.debug`` without ``exc_info``.
    Below INFO nothing is emitted in any deployed environment, and without
    ``exc_info`` there is no ``error_type``/``error_message``/``error_traceback``
    even when it is. Both halves have to be wrong for this to fire.

``cancellation-blind-catch``
    ``except BaseException`` (or bare ``except``) that swallows without ever
    mentioning ``CancelledError``. Ordinary cancellation and a genuine crash
    during teardown are not the same event, and collapsing them is how a
    background task that has been dying at every shutdown goes unnoticed.
    ``app/app.py`` has the shape this rule wants: a ``CancelledError`` arm that
    passes, and a ``BaseException`` arm that logs with ``exc_info``.

What is deliberately NOT a violation, because each one already reports:

* any ``raise`` in the handler -- re-raised, so the caller decides;
* any log call carrying ``exc_info=`` at any level (``exc_info=exc`` included);
* any log call above ``debug`` -- INFO is the production floor, so a
  ``warning``/``error`` record is visible even without a traceback;
* ``record_failure``/``record_error``/``record_exception`` -- the
  ``DependencyIncident`` and circuit-breaker instruments, which bound one
  degraded/recovered pair per incident instead of one record per attempt. A
  gate that flagged the codebase's own best pattern would deserve to be ignored;
* a handler that references the bound exception name, e.g.
  ``except Exception as exc: return Failure(exc)`` -- reported through the
  return value rather than swallowed;
* narrow exception types -- ``except DatastoreObjectNotFoundError: return`` is
  a control decision, not a swallow;
* for the first two rules, a ``try`` body with no ``await``/``async with``/
  ``async for``. A broad catch around pure computation -- a coercion guard, a
  parse fallback -- is a different animal, and the incident this gate exists
  for is an outage becoming a silent denial. ``cancellation-blind-catch`` has
  no such requirement: ``except BaseException: pass`` is wrong regardless.

Not a duplicate of ``check_architecture.py``. That gate counts broad catches
per module and asks *how many*; this one asks *does the handler report what it
caught*, over ``app/core`` as well, and forgives every handler above that does.
A file can be clean here and still be at its broad-catch ceiling there.

Known blind spots, so their absence is a decision rather than an oversight:
``with contextlib.suppress(Exception)`` is not an ``ExceptHandler`` and is
invisible to this gate (``storage_phase.py`` uses it three times), as is a
handler whose caller re-raises on its behalf.

Usage::

    uv run python scripts/check_swallowed_errors.py
    uv run python scripts/check_swallowed_errors.py --update-baseline
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
DEFAULT_BASELINE = ROOT / "swallowed-errors-baseline.json"
SCAN_ROOT = ROOT / "app"
EXCLUDED_PARTS = ("tests", "test_support")

BROAD_NAMES = {"Exception", "BaseException"}
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
# Everything at or above the production floor is visible without a traceback.
VISIBLE_LOG_METHODS = {"info", "warning", "error", "exception", "critical"}
# The bounded-reporting instruments: DependencyIncident and the two breakers.
INCIDENT_RECORDERS = {"record_failure", "record_error", "record_exception"}


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


def _is_broad(handler: ast.ExceptHandler) -> str | None:
    """The caught name when the handler is broad, else ``None``."""
    if handler.type is None:
        return "bare"
    if isinstance(handler.type, ast.Name) and handler.type.id in BROAD_NAMES:
        return handler.type.id
    if isinstance(handler.type, ast.Tuple):
        # Also covers PEP 758's unparenthesised `except A, B:`, which parses
        # to the same Tuple node.
        for element in handler.type.elts:
            if isinstance(element, ast.Name) and element.id in BROAD_NAMES:
                return element.id
    return None


def _own_nodes(node: ast.AST):
    """Walk ``node`` without descending into nested function bodies.

    A ``raise`` inside a closure defined in the handler re-raises nothing, and
    a ``logger.error`` there does not run when the handler does.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            continue
        yield child
        yield from _own_nodes(child)


def _log_calls(handler: ast.ExceptHandler) -> list[ast.Call]:
    calls = []
    for node in _own_nodes(handler):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
        ):
            calls.append(node)
    return calls


def _reports(handler: ast.ExceptHandler) -> bool:
    """Whether the handler makes the failure knowable to somebody."""
    for node in _own_nodes(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in INCIDENT_RECORDERS:
                return True
        # `except Exception as exc: return Failure(exc)` reports through the
        # return value, which is a different -- and fine -- way to be honest.
        if handler.name and isinstance(node, ast.Name) and node.id == handler.name:
            return True
    for call in _log_calls(handler):
        if any(keyword.arg == "exc_info" for keyword in call.keywords):
            return True
        if isinstance(call.func, ast.Attribute) and call.func.attr in (
            VISIBLE_LOG_METHODS
        ):
            return True
    return False


def _awaits(node: ast.Try) -> bool:
    """Whether the guarded body actually does I/O-shaped work."""
    return any(
        isinstance(child, (ast.Await, ast.AsyncWith, ast.AsyncFor))
        for statement in node.body
        for child in ast.walk(statement)
    )


def _event_name(handler: ast.ExceptHandler) -> str:
    for call in _log_calls(handler):
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0].value
            if isinstance(value, str):
                return value
        return "<dynamic>"
    return "<none>"


def _outcome(handler: ast.ExceptHandler) -> str:
    """How the handler ends, as a stable word for the baseline key."""
    last = handler.body[-1]
    if isinstance(last, ast.Pass):
        return "pass"
    if isinstance(last, ast.Return):
        return "return"
    if isinstance(last, ast.Continue):
        return "continue"
    if isinstance(last, ast.Break):
        return "break"
    if isinstance(last, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return "assign"
    return "fallthrough"


class SwallowedErrorChecker(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.violations: list[Violation] = []
        self._scope: list[str] = []

    def _scope_name(self) -> str:
        return ".".join(self._scope) or "<module>"

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

    def visit_Try(self, node: ast.Try) -> None:
        guards_io = _awaits(node)
        mentions_cancellation = any(
            "CancelledError" in ast.dump(handler.type)
            for handler in node.handlers
            if handler.type is not None
        )
        for handler in node.handlers:
            caught = _is_broad(handler)
            if caught is None or _reports(handler):
                continue
            self._report(handler, caught, guards_io, mentions_cancellation)
        self.generic_visit(node)

    def _report(
        self,
        handler: ast.ExceptHandler,
        caught: str,
        guards_io: bool,
        mentions_cancellation: bool,
    ) -> None:
        outcome = _outcome(handler)
        if caught in ("BaseException", "bare") and not mentions_cancellation:
            self._add(handler, "cancellation-blind-catch", f"{caught} -> {outcome}")
            return
        if not guards_io:
            return
        event = _event_name(handler)
        if event in ("<none>",):
            self._add(handler, "silent-broad-catch", f"{caught} -> {outcome}")
        else:
            self._add(handler, "debug-only-broad-catch", event)

    def _add(self, handler: ast.ExceptHandler, rule: str, detail: str) -> None:
        self.violations.append(
            Violation(
                path=self.path,
                line=handler.lineno,
                scope=self._scope_name(),
                rule=rule,
                detail=detail,
            )
        )


def collect(paths: list[Path]) -> list[Violation]:
    found: list[Violation] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        checker = SwallowedErrorChecker(str(path.relative_to(ROOT)))
        checker.visit(tree)
        found.extend(checker.violations)
    return sorted(found, key=lambda v: (v.path, v.line, v.rule))


def source_files() -> list[Path]:
    return sorted(
        path
        for path in SCAN_ROOT.rglob("*.py")
        if not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def _load_baseline(path: Path) -> dict[str, int]:
    """Read the baseline, accepting the older list form (one occurrence each)."""
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
                "Pre-existing broad excepts that leave no trace of what they "
                "caught: no log at all, or a debug record with no exc_info that "
                "production never emits. This file may shrink freely; growing it "
                "means a new failure has been made invisible. See "
                "scripts/check_swallowed_errors.py."
            ),
            "violations": dict(sorted(Counter(v.key() for v in violations).items())),
        }
        args.baseline.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"✓ baseline written: {sum(payload['violations'].values())} entries")
        return 0

    baseline = _load_baseline(args.baseline)

    # Counted, not a set: the key carries no line number so edits above a
    # violation do not churn the file, which would otherwise let a second
    # identical handler in an already-baselined function through unreported.
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
            f"✓ swallowed errors: no new violations ({sum(baseline.values())} baselined)"
        )
        return 0

    print(f"✗ swallowed errors: {len(new)} new violation(s)\n")
    for violation in new:
        print(f"  {violation.render()}")
    print(
        "\nSay what was caught: log at error/warning with exc_info=True, record it "
        "on a DependencyIncident, re-raise it, or return it to the caller."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
