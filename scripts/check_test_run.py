#!/usr/bin/env python3
"""Fail when a lane's tests skipped themselves at runtime instead of running.

`check_pytest_census.py` is the collection-time half: it proves the tests still
exist, are still collected, and are not being selected into a lane that reports
"passed" while they only ever skip. It cannot see a test that is collected,
*selected*, starts, and then calls `pytest.skip()` from a fixture -- because by
then collection is long over.

That gap is not hypothetical and it is not small. 72 tests in
`app/modules/workspace/tests/integration/` skip themselves when Postgres is
unreachable, from `conftest.py`. They are in the unit lane deliberately -- they
are meant to run against the `postgres` service the job starts. If that service
regresses (a bad tag, a health-check timeout, a port clash) all 72 skip, the
lane is green, and the census says "no lane skipping green" because from its
side nothing changed.

ci.yml already records what that costs: the same suite silently skipped 57
tests, "which is how a sweep that deleted live user workspaces reached
production with 24 tests written about it".

So this reads the JUnit XML the run actually produced and compares the skip
count against a committed ceiling:

    python3 scripts/check_test_run.py lemma-backend/coverage-backend/junit-unit.xml
    python3 scripts/check_test_run.py --update <path>

A ceiling, not a floor -- the opposite direction to the census, because this is
about skips growing rather than tests vanishing. Removing a skip never fails.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / ".github" / "test-run-baseline.json"


def summarise(report: Path) -> dict[str, int]:
    """Totals from a JUnit file, summed over its suites."""
    root = ElementTree.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise SystemExit(f"{report} contains no <testsuite>")
    totals = {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    return totals


def skipped_names(report: Path) -> list[str]:
    """Which tests skipped, most useful first: the ones that skip in bulk."""
    root = ElementTree.parse(report).getroot()
    return sorted(
        f"{case.get('classname', '')}::{case.get('name', '')}"
        for case in root.iter("testcase")
        if case.find("skipped") is not None
    )


def check(lane: str, totals: dict[str, int], baseline: dict) -> list[str]:
    recorded = baseline.get(lane)
    if recorded is None:
        return [
            f"no ceiling recorded for lane {lane!r}. Run with --update once you "
            f"have a run whose skips you have actually looked at"
        ]
    problems = []
    if totals["skipped"] > recorded["skipped"]:
        problems.append(
            f"{totals['skipped']} tests skipped themselves at runtime, up from "
            f"{recorded['skipped']}. A test that skips reports the same green as "
            f"a test that passes -- if a service this lane depends on stopped "
            f"coming up, this is what it looks like. Re-record with --update if "
            f"the increase is deliberate"
        )
    # A lane whose test count collapses has usually lost a whole directory, and
    # the census only sees that at collection time in the *backend* tree.
    if totals["tests"] < recorded["tests"]:
        problems.append(
            f"{totals['tests']} tests ran, down from {recorded['tests']}"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="a pytest --junitxml file")
    parser.add_argument("--lane", default="unit", help="which lane this report is")
    parser.add_argument(
        "--update", action="store_true", help="record this run as the ceiling"
    )
    arguments = parser.parse_args()

    if not arguments.report.is_file():
        raise SystemExit(f"no JUnit report at {arguments.report}")
    totals = summarise(arguments.report)

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    if arguments.update:
        baseline[arguments.lane] = {
            "tests": totals["tests"],
            "skipped": totals["skipped"],
        }
        baseline["_comment"] = (
            "Ceilings for runtime skips, per lane. A test that skips reports the "
            "same green as one that passes, and collection-time checks cannot "
            "see it. Regenerate with `python3 scripts/check_test_run.py --update "
            "--lane <lane> <junit.xml>`."
        )
        BASELINE.write_text(json.dumps(dict(sorted(baseline.items())), indent=2) + "\n")
        print(
            f"Recorded lane {arguments.lane!r}: {totals['tests']} tests, "
            f"{totals['skipped']} skipped."
        )
        return 0

    problems = check(arguments.lane, totals, baseline)
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        # The names, because "12 more skipped" without them is a puzzle.
        names = skipped_names(arguments.report)
        print("::group::tests that skipped", file=sys.stderr)
        for name in names[:60]:
            print(f"  {name}", file=sys.stderr)
        if len(names) > 60:
            print(f"  ... and {len(names) - 60} more", file=sys.stderr)
        print("::endgroup::", file=sys.stderr)
        return 1
    print(
        f"Lane {arguments.lane!r}: {totals['tests']} tests, "
        f"{totals['skipped']} skipped, within the recorded ceiling."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
