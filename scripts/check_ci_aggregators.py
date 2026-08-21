#!/usr/bin/env python3
"""Assert every aggregator job watches every job in its workflow.

"CI passed" and "Backend E2E passed" are the checks the branch ruleset
requires. They are only as good as their `needs:` list, and that list is
hand-written: ci.yml already carries a comment recording the time a job went
missing from it and a wheel shipped without its skills while the aggregator
stayed green.

Also checks that every job declares `timeout-minutes`. Without one a job runs
to GitHub's six-hour default, which is not a timeout so much as a way to
discover a hang the next morning.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# workflow file -> the job id that aggregates it into one required check.
AGGREGATORS = {
    "ci.yml": "ci",
    "e2e.yml": "e2e-passed",
}

# Jobs that legitimately stand outside their workflow's aggregator: they are
# opt-in lanes that a normal PR never runs, so requiring them would leave the
# aggregator waiting forever.
EXEMPT = {
    ("e2e.yml", "surface-live-smoke"),
}

# Every workflow is checked for timeouts, not just the two with aggregators.
# Release workflows are exempt: their jobs legitimately run for well over an
# hour and a wrong bound there fails a release rather than a PR.
# Empty on purpose. The release workflows were exempt because they had no
# bounds; they have them now, so nothing needs an exemption and a new workflow
# cannot quietly acquire one.
NO_TIMEOUT_REQUIRED: set[str] = set()


def main() -> int:
    problems: list[str] = []

    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = yaml.safe_load(path.read_text())
        jobs = document.get("jobs") or {}

        if path.name not in NO_TIMEOUT_REQUIRED:
            for name, job in jobs.items():
                if "timeout-minutes" not in job:
                    problems.append(
                        f"{path.name}: job '{name}' has no timeout-minutes, so it "
                        f"runs to GitHub's 6-hour default"
                    )

        aggregator = AGGREGATORS.get(path.name)
        if aggregator is None:
            continue
        if aggregator not in jobs:
            problems.append(f"{path.name}: expected an aggregator job '{aggregator}'")
            continue

        needs = jobs[aggregator].get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        watched = set(needs)
        for name in jobs:
            if name == aggregator or (path.name, name) in EXEMPT:
                continue
            if name not in watched:
                problems.append(
                    f"{path.name}: job '{name}' is not in '{aggregator}'.needs, so it "
                    f"can fail while the required check stays green"
                )

    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print(f"Aggregator and timeout checks passed "
          f"({len(list(WORKFLOWS.glob('*.yml')))} workflows).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
