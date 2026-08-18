#!/usr/bin/env python3
"""Which module e2e tests the scenario suite might already cover.

The two suites overlap and the overlap should shrink — but "this looks like the
same thing" is not a reason to delete a test, and an argument nobody can
reproduce is not an argument. This makes the cheap half of the case mechanical,
so a reviewer can spend their attention on the half that is not.

**It answers exactly one of the five questions** in the deletion policy (see
`docs/testing.md`): *does a scenario exercise every operation this e2e test
did?* It says nothing about whether the scenario asserts as much, and that is
where deletions are actually decided. A file this reports as a candidate can
still be entirely wrong to delete — `pod_bundle/tests/e2e/test_export_e2e.py`
pins the bundle format field by field, and the scenario that "covers" it proves
only that the exporter and the importer agree with each other.

So this is a **report, not a gate**. It narrows the list. It does not authorise
anything.

    cd lemma-backend && uv run python ../scripts/check_e2e_scenario_overlap.py
    cd lemma-backend && uv run python ../scripts/check_e2e_scenario_overlap.py --module pod --verbose

Run it through the backend's interpreter: the tests it parses use Python 3.14
syntax that earlier versions reject outright.

How it works: the committed OpenAPI spec maps every (method, path template) to
an operation id. An AST pass over the e2e tests reconstructs the paths they call
and matches them against those templates; another over `tests/scenarios/`
collects what `@covers` declares. A file is a *candidate* when every operation
it touches is declared by some scenario, and it carries none of the four things
a scenario provably cannot do.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "lemma-python" / "lemma_sdk" / "openapi_spec.json"
E2E_ROOT = ROOT / "lemma-backend" / "app" / "modules"
SCENARIOS = ROOT / "tests" / "scenarios" / "journeys"

#: Modules whose e2e coverage carries its own floor in `e2e.yml`. Deletions here
#: move a gate directly, and `function` is small enough that one test can move it
#: a whole point — so they are reported separately and never in bulk.
FLOORED = ("agent", "agent_surfaces", "datastore", "function")

#: The four things a black-box scenario provably cannot do. Any one of them
#: means the e2e test is asserting something no scenario replaces, whatever the
#: operation overlap says.
DISQUALIFIERS = {
    "fault injection": re.compile(r"\bmonkeypatch\.|\bAsyncMock\b|\bMagicMock\b|\bpatch\("),
    "exact refusal code": re.compile(r"status_code\s*==\s*[45]\d\d"),
    "database post-condition": re.compile(r"\bdb_session\b|\buow\b|\brepository\b"),
    "internal import": re.compile(r"^\s*from app\.(?!modules\.test_support)", re.M),
}

_HTTP_VERBS = {"get", "post", "put", "patch", "delete"}


@dataclass
class Candidate:
    path: Path
    tests: int
    operations: set[str] = field(default_factory=set)
    unmatched: set[str] = field(default_factory=set)
    disqualifiers: list[str] = field(default_factory=list)

    @property
    def module(self) -> str:
        return self.path.relative_to(E2E_ROOT).parts[0]

    @property
    def is_candidate(self) -> bool:
        return not self.unmatched and not self.disqualifiers


def operation_index() -> list[tuple[str, re.Pattern[str], str]]:
    """(method, path-template-as-regex, operationId) for every documented route."""
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    index: list[tuple[str, re.Pattern[str], str]] = []
    for template, operations in spec["paths"].items():
        # `{pod_id}` matches one path segment, whatever the test interpolated.
        pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", re.escape(template)
                                          .replace(r"\{", "{").replace(r"\}", "}")) + "$")
        for method, operation in operations.items():
            if method in _HTTP_VERBS and operation.get("operationId"):
                index.append((method, pattern, operation["operationId"]))
    return index


def _literal_path(node: ast.AST) -> str | None:
    """The request path a call site names, when it can be read statically.

    f-strings become their literal parts with every interpolation replaced by a
    single segment, which is exactly what the route templates match.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            else:
                out.append("\x00")  # one interpolated segment
        return "".join(out).replace("\x00", "x")
    return None


def operations_called(tree: ast.AST, index) -> tuple[set[str], set[str]]:
    """Operation ids a module touches, and the paths that matched nothing."""
    found: set[str] = set()
    unmatched: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr.lower()
        if method not in _HTTP_VERBS or not node.args:
            continue
        path = _literal_path(node.args[0])
        if path is None:
            continue
        path = path.split("?")[0].rstrip("/") or "/"
        matches = {oid for verb, pattern, oid in index
                   if verb == method and pattern.match(path)}
        if matches:
            found |= matches
        elif path.startswith("/"):
            unmatched.add(f"{method.upper()} {path}")
    return found, unmatched


def covered_by_scenarios() -> set[str]:
    """Every operation id and event the scenario suite declares with `@covers`."""
    declared: set[str] = set()
    for file in SCENARIOS.rglob("test_*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "covers"):
                declared |= {a.value for a in node.args
                             if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return declared


def survey() -> list[Candidate]:
    index = operation_index()
    declared = covered_by_scenarios()
    results: list[Candidate] = []

    for file in sorted(E2E_ROOT.glob("*/tests/e2e/**/test_*.py")):
        source = file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        found, unmatched = operations_called(tree, index)
        tests = sum(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")
            for n in ast.walk(tree)
        )
        results.append(Candidate(
            path=file,
            tests=tests,
            operations=found,
            unmatched=(found - declared) | unmatched,
            disqualifiers=[why for why, rule in DISQUALIFIERS.items() if rule.search(source)],
        ))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", help="only this backend module")
    parser.add_argument("--verbose", action="store_true", help="say why each was excluded")
    args = parser.parse_args()

    results = [c for c in survey() if not args.module or c.module == args.module]
    candidates = [c for c in results if c.is_candidate]

    open_modules = [c for c in candidates if c.module not in FLOORED]
    floored = [c for c in candidates if c.module in FLOORED]

    print(f"{len(results)} e2e files examined, "
          f"{sum(c.tests for c in results)} tests\n")

    print(f"Candidates outside the floored modules — {len(open_modules)} files, "
          f"{sum(c.tests for c in open_modules)} tests")
    for c in sorted(open_modules, key=lambda c: (c.module, c.path.name)):
        print(f"  {c.path.relative_to(ROOT)}  ({c.tests} tests)")

    print(f"\nCandidates inside {', '.join(FLOORED)} — {len(floored)} files, "
          f"{sum(c.tests for c in floored)} tests")
    print("  These carry e2e-only coverage floors. One file at a time, measured.")
    for c in sorted(floored, key=lambda c: (c.module, c.path.name)):
        print(f"  {c.path.relative_to(ROOT)}  ({c.tests} tests)")

    if args.verbose:
        print("\nExcluded, and why:")
        for c in sorted(results, key=lambda c: (c.module, c.path.name)):
            if c.is_candidate:
                continue
            reasons = list(c.disqualifiers)
            if c.unmatched:
                reasons.append(f"{len(c.unmatched)} operations no scenario covers")
            print(f"  {c.path.relative_to(ROOT)}: {'; '.join(reasons)}")

    print("\nA candidate is a file to *examine*, never a file to delete. It has "
          "passed one\nof the five questions in docs/testing.md — the cheap one. "
          "The other four are\nnot mechanical.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
