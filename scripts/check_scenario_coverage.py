#!/usr/bin/env python3
"""Hold the product specification and the scenario suite to each other.

The specification in ``docs/product`` is only a specification because these
checks run. Without them a scenario can claim to prove something that was never
written down, a promise can claim to be covered by a test that does not exist,
and both drift silently until the document is decoration.

Six gates, all cheap enough to sit in ``make quality``:

1. Every ``@proves`` id in the suite names a scenario that exists.
2. Every contract reference — ``@covers`` in a test, ``**Contracts:**`` in the
   specification — names a live OpenAPI operation or a live analytics event.
3. Every scenario marked ``covered`` has at least one test proving it.
4. Every scenario marked ``gap`` carries a ``> **Gap:**`` note saying how the
   implementation diverges. The detail lives in ``issues.md``; what has to stay
   beside the promise is the admission.
5. Every promise named by an ``issues.md`` entry is marked ``gap``. Without
   this the register and the specification drift apart in the one direction
   that flatters us: a promise reads ``covered`` while a committed entry says
   the system does not keep it.
6. ``docs/product/coverage.md`` matches what the sources produce.

Run with ``--write`` to regenerate the coverage document, ``--check`` (the
default) to verify it.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_DIR = ROOT / "docs" / "product"
JOURNEY_DIR = SPEC_DIR / "journeys"
COVERAGE_DOC = SPEC_DIR / "coverage.md"
SUITE_DIR = ROOT / "tests" / "scenarios"
OPENAPI = ROOT / "lemma-python" / "lemma_sdk" / "openapi_spec.json"
ANALYTICS = (
    ROOT / "lemma-backend" / "app" / "core" / "analytics" / "event_catalog.py"
)

VALID_STATUSES = {"covered", "planned", "gap", "manual", "withdrawn"}

SCENARIO_RE = re.compile(r"^### (PS-[A-Z]+-\d+) — (.+?)\s*$", re.M)
STATUS_RE = re.compile(r"^\*\*Status:\*\* (\w+)\s*$", re.M)
CONTRACTS_RE = re.compile(r"^\*\*Contracts:\*\* (.+?)\s*$", re.M)
CAPABILITY_RE = re.compile(r"^## Capability: (.+?)\s*$", re.M)
JOURNEY_RE = re.compile(r"^\*\*Journey:\*\* (.+?)\s*$", re.M)
GAP_NOTE_RE = re.compile(r"^> \*\*Gap:\*\*", re.M)
BACKTICKED = re.compile(r"`([a-z_][a-z0-9_.]*)`")


@dataclass
class Scenario:
    id: str
    title: str
    status: str
    journey: str
    journey_file: str
    capability: str
    contracts: list[str] = field(default_factory=list)
    has_gap_note: bool = False


@dataclass
class ScenarioTest:
    name: str
    file: str
    proves: list[str] = field(default_factory=list)
    covers: list[str] = field(default_factory=list)


def _blocks(text: str, pattern: re.Pattern[str]) -> list[tuple[str, str, int]]:
    """Split ``text`` on ``pattern``, returning (head, body, offset) per match."""
    matches = list(pattern.finditer(text))
    out = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append((match.group(1), text[match.start() : end], match.start()))
    return out


def load_scenarios() -> tuple[list[Scenario], list[str]]:
    scenarios: list[Scenario] = []
    errors: list[str] = []
    if not JOURNEY_DIR.is_dir():
        return scenarios, [f"No journey directory at {JOURNEY_DIR}"]

    for path in sorted(JOURNEY_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        journey_match = JOURNEY_RE.search(text)
        if not journey_match:
            errors.append(f"{rel}: no '**Journey:**' line")
        journey = path.stem.replace("-", " ").capitalize()

        # Which capability a scenario sits under is decided by position, so walk
        # capabilities and scenarios over the same text and match by offset.
        capabilities = _blocks(text, CAPABILITY_RE)

        def capability_at(offset: int) -> str:
            current = "(none)"
            for name, _, start in capabilities:
                if start < offset:
                    current = name
            return current

        for scenario_id, body, offset in _blocks(text, SCENARIO_RE):
            title_match = SCENARIO_RE.search(body)
            title = title_match.group(2) if title_match else ""
            statuses = STATUS_RE.findall(body)
            if len(statuses) != 1:
                errors.append(
                    f"{rel}: {scenario_id} has {len(statuses)} '**Status:**' lines, expected 1"
                )
                continue
            status = statuses[0]
            if status not in VALID_STATUSES:
                errors.append(
                    f"{rel}: {scenario_id} has unknown status {status!r} "
                    f"(expected one of {', '.join(sorted(VALID_STATUSES))})"
                )
                continue
            contracts_match = CONTRACTS_RE.search(body)
            contracts = (
                BACKTICKED.findall(contracts_match.group(1)) if contracts_match else []
            )
            scenarios.append(
                Scenario(
                    id=scenario_id,
                    title=title,
                    status=status,
                    journey=journey,
                    journey_file=path.name,
                    capability=capability_at(offset),
                    contracts=contracts,
                    has_gap_note=bool(GAP_NOTE_RE.search(body)),
                )
            )

    seen: dict[str, str] = {}
    for scenario in scenarios:
        if scenario.id in seen:
            errors.append(
                f"Duplicate scenario id {scenario.id} in {scenario.journey_file} "
                f"and {seen[scenario.id]}"
            )
        seen[scenario.id] = scenario.journey_file
    return scenarios, errors


def _decorator_strings(node: ast.AST, wanted: str) -> list[str]:
    """String arguments of ``@wanted(...)``, however it is qualified."""
    found: list[str] = []
    for decorator in getattr(node, "decorator_list", []):
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name != wanted:
            continue
        for arg in decorator.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append(arg.value)
    return found


def load_tests() -> tuple[list[ScenarioTest], list[str]]:
    tests: list[ScenarioTest] = []
    errors: list[str] = []
    if not SUITE_DIR.is_dir():
        return tests, errors

    for path in sorted(SUITE_DIR.rglob("test_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{rel}: cannot parse ({exc})")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            proves = _decorator_strings(node, "proves")
            covers = _decorator_strings(node, "covers")
            if not proves and not covers:
                continue
            tests.append(
                ScenarioTest(name=node.name, file=rel, proves=proves, covers=covers)
            )
    return tests, errors


def load_known_contracts() -> tuple[set[str], set[str], list[str]]:
    errors: list[str] = []
    operations: set[str] = set()
    if OPENAPI.is_file():
        spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
        for path_item in spec.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict) and "operationId" in operation:
                    operations.add(operation["operationId"])
    else:
        errors.append(f"Missing OpenAPI specification at {OPENAPI}")

    events: set[str] = set()
    if ANALYTICS.is_file():
        events = set(
            re.findall(
                r'^\s{4}"([a-z_]+\.[a-z_]+)":\s*AnalyticEvent',
                ANALYTICS.read_text(encoding="utf-8"),
                re.M,
            )
        )
    else:
        errors.append(f"Missing analytics catalog at {ANALYTICS}")
    return operations, events, errors


#: Filled in by `main` before rendering, so `render_coverage` stays a pure
#: function of what it is handed plus the live surface.
OPERATIONS: set[str] = set()
EVENTS: set[str] = set()


def render_coverage(
    scenarios: list[Scenario], tests: list[ScenarioTest]
) -> str:
    by_scenario: dict[str, list[ScenarioTest]] = defaultdict(list)
    for test in tests:
        for scenario_id in test.proves:
            by_scenario[scenario_id].append(test)

    counts: dict[str, int] = defaultdict(int)
    for scenario in scenarios:
        counts[scenario.status] += 1

    lines = [
        "# Scenario coverage",
        "",
        "Generated by `scripts/check_scenario_coverage.py`. Do not edit by hand;",
        "run `make scenario-coverage` to refresh it.",
        "",
        "Every promise in [the product specification](README.md) and the scenario",
        "tests proving it. A promise with no test is not a failure on its own —",
        "only a promise marked `covered` with no test is.",
        "",
        "## Totals",
        "",
        "| Status | Scenarios |",
        "| --- | ---: |",
    ]
    for status in sorted(VALID_STATUSES):
        lines.append(f"| `{status}` | {counts.get(status, 0)} |")
    lines += [
        f"| **total** | **{len(scenarios)}** |",
        "",
        f"Scenario tests declaring a promise: {len(tests)}.",
        "",
    ]

    # Which API operations and events the suite actually exercises. Distinct
    # from scenario coverage: a promise can be covered while operations it does
    # not name go untouched, and that gap is worth seeing.
    exercised: set[str] = {name for test in tests for name in test.covers}
    lines += [
        "## Contract coverage",
        "",
        "How much of the API and event surface the scenarios touch, counted from",
        "`@covers`. An operation with no scenario is not necessarily untested —",
        "the module suites may cover it — but it is untested *as product*.",
        "",
        "| Surface | Exercised | Total |",
        "| --- | ---: | ---: |",
    ]
    lines.append(
        f"| OpenAPI operations | {len(exercised & OPERATIONS)} | {len(OPERATIONS)} |"
    )
    lines.append(f"| Product events | {len(exercised & EVENTS)} | {len(EVENTS)} |")
    lines.append("")

    by_journey: dict[str, list[Scenario]] = defaultdict(list)
    for scenario in scenarios:
        by_journey[scenario.journey_file].append(scenario)

    for journey_file in sorted(by_journey):
        group = by_journey[journey_file]
        lines += [
            f"## [{group[0].journey}](journeys/{journey_file})",
            "",
            "| Scenario | Status | Proven by |",
            "| --- | --- | --- |",
        ]
        for scenario in group:
            proving = by_scenario.get(scenario.id, [])
            proof = (
                ", ".join(f"`{test.name}`" for test in proving) if proving else "—"
            )
            lines.append(
                f"| `{scenario.id}` {scenario.title} | `{scenario.status}` | {proof} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate docs/product/coverage.md instead of verifying it",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify (the default when --write is absent)",
    )
    args = parser.parse_args()

    scenarios, errors = load_scenarios()
    tests, test_errors = load_tests()
    errors += test_errors
    operations, events, contract_errors = load_known_contracts()
    errors += contract_errors
    known = operations | events

    global OPERATIONS, EVENTS
    OPERATIONS, EVENTS = operations, events

    by_id = {scenario.id: scenario for scenario in scenarios}

    # Gate 1 — every @proves names a real scenario.
    for test in tests:
        for scenario_id in test.proves:
            if scenario_id not in by_id:
                errors.append(
                    f"{test.file}::{test.name} proves {scenario_id}, "
                    f"which is not in docs/product/journeys"
                )

    # Gate 2 — every contract reference names something that exists.
    for test in tests:
        for name in test.covers:
            if name not in known:
                errors.append(
                    f"{test.file}::{test.name} covers {name!r}, which is neither an "
                    f"OpenAPI operationId nor an analytics event"
                )
    for scenario in scenarios:
        for name in scenario.contracts:
            if name not in known:
                errors.append(
                    f"{scenario.journey_file}: {scenario.id} lists contract {name!r}, "
                    f"which is neither an OpenAPI operationId nor an analytics event"
                )

    # Gate 3 — covered means a test exists.
    proven = {scenario_id for test in tests for scenario_id in test.proves}
    for scenario in scenarios:
        if scenario.status == "covered" and scenario.id not in proven:
            errors.append(
                f"{scenario.journey_file}: {scenario.id} is marked covered but no "
                f"scenario test proves it"
            )

    # Gate 4 — a gap admits how it diverges.
    for scenario in scenarios:
        if scenario.status == "gap" and not scenario.has_gap_note:
            errors.append(
                f"{scenario.journey_file}: {scenario.id} is marked gap but carries no "
                f"'> **Gap:**' note saying how the implementation diverges"
            )

    # Gate 5 — a promise the register says is broken is not still `covered`.
    # The two documents are written at different times by different people, and
    # nothing else compares them: a finding lands in issues.md, the promise it
    # names keeps saying `covered`, and the specification quietly overstates
    # what the system does.
    register = ROOT / "issues.md"
    if register.is_file():
        for line in register.read_text(encoding="utf-8").splitlines():
            if not line.startswith("**Violates:**"):
                continue
            for scenario_id in re.findall(r"PS-[A-Z]+-\d+", line):
                scenario = by_id.get(scenario_id)
                if scenario is None:
                    errors.append(
                        f"issues.md: '{line.strip()}' names {scenario_id}, which is "
                        f"not in docs/product/journeys"
                    )
                elif scenario.status == "covered":
                    errors.append(
                        f"issues.md names {scenario_id} as violated, but "
                        f"{scenario.journey_file} still marks it covered — mark it "
                        f"gap with a '> **Gap:**' note, or drop it from the entry "
                        f"if the entry does not actually break that promise"
                    )

    # Gate 6 — the coverage document is current.
    rendered = render_coverage(scenarios, tests)
    if args.write:
        COVERAGE_DOC.parent.mkdir(parents=True, exist_ok=True)
        COVERAGE_DOC.write_text(rendered, encoding="utf-8")
        print(f"Wrote {COVERAGE_DOC.relative_to(ROOT)}")
    else:
        current = (
            COVERAGE_DOC.read_text(encoding="utf-8") if COVERAGE_DOC.is_file() else ""
        )
        if current != rendered:
            errors.append(
                "docs/product/coverage.md is stale; run `make scenario-coverage`"
            )

    if errors:
        print("Scenario coverage check failed:\n", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    covered = sum(1 for s in scenarios if s.status == "covered")
    gaps = sum(1 for s in scenarios if s.status == "gap")
    exercised = {name for test in tests for name in test.covers}
    print(
        f"✓ scenario coverage: {len(scenarios)} scenarios "
        f"({covered} covered, {gaps} gap), {len(tests)} scenario tests\n"
        f"  contract coverage: {len(exercised & operations)}/{len(operations)} "
        f"operations, {len(exercised & events)}/{len(events)} events"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
