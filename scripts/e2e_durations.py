#!/usr/bin/env python3
"""Report — and bound — how long individual e2e tests take.

Half of the backend e2e suite's wall-clock used to sit in a tenth of its tests,
and a single test accounted for 5.6% of the whole run on its own. Two modes,
because both halves of that problem are real:

  --summary  render a shard's slow tail into the GitHub job summary, so the
             tail stays visible instead of living in `--durations` output that
             nobody scrolls to.

  --check    fail the shard when a test exceeds the PR-lane budget without
             being on the baseline. A long test is not a bug, but a long test
             *in front of the merge button* is a choice, and it should be one
             somebody made on purpose. The way out is `@pytest.mark.slow`,
             which moves it to the scheduled protected lane.

Only fast-lane tests reach this: anything marked `slow` is already filtered out
before pytest writes the JUnit, so every test seen here is one a PR waits for.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / ".github" / "e2e-slow-baseline.json"

#: A PR-lane test above this is worth a deliberate decision. Set from measured
#: data rather than taste: with the baselined exception below, the slowest
#: fast-lane test is under 35s, so this leaves real headroom while still
#: catching anything in the class that made one test 5.6% of the suite.
DEFAULT_BUDGET_SECONDS = 45.0

#: Above this a single test is worth a look on its own — roughly a percent of a
#: balanced shard.
NOTABLE_SECONDS = 15.0


def _cases(paths: list[Path], *, passing_only: bool = False) -> list[tuple[float, str, str]]:
    cases: list[tuple[float, str, str]] = []
    for path in paths:
        if not path.exists():
            continue
        for case in ET.parse(path).getroot().iter("testcase"):
            # A failed test's duration is not a measurement of anything. A test
            # that blows a 90-second condition wait reports 92s and would trip
            # the budget, so one real failure became two red steps and the
            # second one pointed at the wrong problem.
            if passing_only and any(
                child.tag in ("failure", "error") for child in case
            ):
                continue
            cases.append((
                float(case.get("time") or 0),
                case.get("classname", ""),
                case.get("name", ""),
            ))
    cases.sort(reverse=True)
    return cases


def summarize(cases: list[tuple[float, str, str]], top: int) -> int:
    total = sum(seconds for seconds, _, _ in cases)
    tail = sum(seconds for seconds, _, _ in cases[:top])
    print(f"### Slowest tests ({len(cases)} tests, {total:.0f}s of test time)")
    print()
    print(f"The top {min(top, len(cases))} account for "
          f"**{tail / total * 100:.0f}%** of this shard's test time.")
    print()
    print("| | Test | Seconds |")
    print("|---|---|---|")
    for seconds, classname, name in cases[:top]:
        flag = "🐢" if seconds >= NOTABLE_SECONDS else ""
        print(f"| {flag} | `{classname.rsplit('.', 1)[-1]}::{name}` | {seconds:.1f} |")
    return 0


def check(cases: list[tuple[float, str, str]], budget: float) -> int:
    allowed = {
        entry["test"]: entry
        for entry in json.loads(BASELINE.read_text())["allowed"]
    }
    over = [
        (seconds, f"{classname}::{name}")
        for seconds, classname, name in cases
        if seconds > budget
    ]
    new = [(seconds, test) for seconds, test in over if test not in allowed]
    for seconds, test in new:
        print(f"::error::{test} took {seconds:.1f}s, over the {budget:.0f}s PR-lane "
              f"budget. Mark it @pytest.mark.slow to move it to the scheduled "
              f"protected lane, split the fast half out of it, or add it to "
              f"{BASELINE.relative_to(REPO_ROOT)} with a reason.")
    if new:
        return 1
    baselined = [(seconds, test) for seconds, test in over if test in allowed]
    for seconds, test in baselined:
        print(f"  baselined: {seconds:6.1f}s  {test.rsplit('::', 1)[-1]}"
              f"  — {allowed[test]['why']}")
    print(f"e2e duration budget: {len(cases)} tests, none over {budget:.0f}s "
          f"({len(baselined)} baselined).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path, nargs="+")
    parser.add_argument("--check", action="store_true",
                        help="enforce the PR-lane duration budget")
    parser.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    cases = _cases(args.junit, passing_only=args.check)
    if not cases:
        # In --check this is the correct answer for a shard whose every test
        # failed: the run is already red for a better reason.
        print(f"no passing testcases found in {[str(p) for p in args.junit]}",
              file=sys.stderr)
        return 0 if args.check else 1
    return (check(cases, args.budget_seconds) if args.check
            else summarize(cases, args.top))


if __name__ == "__main__":
    raise SystemExit(main())
