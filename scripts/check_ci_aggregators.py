#!/usr/bin/env python3
"""Assert every aggregator job watches every job in its workflow.

"CI passed", "Backend E2E passed" and "Security passed" are the checks the
branch ruleset is meant to require. They are only as good as their `needs:`
list, and that list is hand-written: ci.yml already carries a comment recording
the time a job went missing from it and a wheel shipped without its skills
while the aggregator stayed green.

This file cannot see the ruleset, and the ruleset has been wrong. As of the CI
audit it required "lemma-backend unit" and "Backend E2E passed" -- so the whole
of ci.yml except one job, and the whole of security.yml, could go red without
blocking a merge, and "lemma-backend unit" is path-filtered anyway, meaning it
reported `skipped` (which GitHub counts as satisfied) on every pull request
that did not touch the backend. Keep the three names above and the ruleset in
agreement; there is no gate that can do it for you.

Also checks that every job declares `timeout-minutes`. Without one a job runs
to GitHub's six-hour default, which is not a timeout so much as a way to
discover a hang the next morning.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# workflow file -> the job id that aggregates it into one required check.
AGGREGATORS = {
    "ci.yml": "ci",
    "e2e.yml": "e2e-passed",
    # Added when the ruleset was found to require neither it nor "CI passed".
    # Every job in security.yml is path-filtered, so none of them can be named
    # in a ruleset directly; this is the one that can.
    "security.yml": "security-passed",
}

# Jobs that legitimately stand outside their workflow's aggregator: they are
# opt-in lanes that a normal PR never runs, so requiring them would leave the
# aggregator waiting forever.
EXEMPT = {
    ("e2e.yml", "surface-live-smoke"),
    # Merge-to-main only, and deliberately not a merge gate: it builds the real
    # ~700 MB host pack on a macOS runner and executes its interpreters, which
    # is ~25 minutes and guards release-time breakage rather than review-time
    # correctness. Requiring it would leave every PR's aggregator waiting for a
    # job that never starts, since its `if:` includes `github.event_name ==
    # 'push'`. A red one is "look in the morning".
    ("ci.yml", "host-pack-macos"),
}

# The workflow that announces a failure nobody is looking at. It watches the
# others by name, so a workflow missing from its list is silent -- which is the
# state every scheduled lane in this repository was in.
NOTIFIER = "notify-failure.yml"

# Workflows the notifier deliberately does not watch.
UNWATCHED = {
    # A `workflow_run` consumer, and the only one here. Such a run always
    # reports `head_branch: main` and `event: workflow_run`, whatever commit it
    # is actually about -- so there is no expression the notifier can write that
    # tells "coverage failed on main" from "coverage failed on somebody's pull
    # request", and the second is far more common. Announcing both would put
    # pull-request noise in a channel that exists for the runs nobody sees,
    # which is how a channel stops being read.
    #
    # Affordable because this one is advisory by design: not a required check,
    # off the pull-request critical path, and enforcing module floors several
    # modules are still below. A real regression in it is found by reading it,
    # not by being paged about it.
    "backend-coverage.yml",
}

# Every workflow is checked for timeouts, not just the two with aggregators.
# Release workflows are exempt: their jobs legitimately run for well over an
# hour and a wrong bound there fails a release rather than a PR.
# Empty on purpose. The release workflows were exempt because they had no
# bounds; they have them now, so nothing needs an exemption and a new workflow
# cannot quietly acquire one.
NO_TIMEOUT_REQUIRED: set[str] = set()


def unknown_change_outputs(workflow: str, jobs: dict) -> list[str]:
    """Every `needs.changes.outputs.X` a job gates on must actually exist.

    A typo here is silent and total. GitHub evaluates an unknown output as the
    empty string, so `if: needs.changes.outputs.dekstop == 'true'` is never
    true: the job never runs, it is reported as skipped, and the aggregator
    treats skipped as passing. The result is a required check that is green
    because a suite stopped running -- the exact failure this file exists to
    prevent, one level further out.
    """
    changes = jobs.get("changes") or {}
    declared = set((changes.get("outputs") or {}).keys())
    if not declared:
        return []

    pattern = re.compile(r"needs\.changes\.outputs\.([A-Za-z0-9_-]+)")
    problems = []
    for name, job in jobs.items():
        condition = job.get("if")
        if not isinstance(condition, str):
            continue
        for referenced in pattern.findall(condition):
            if referenced not in declared:
                problems.append(
                    f"{workflow}: job '{name}' gates on "
                    f"needs.changes.outputs.{referenced}, which is not declared. "
                    f"An unknown output is the empty string, so this job never "
                    f"runs and its skip reads as a pass"
                )
    return problems


def literal_node_versions(workflow: str, jobs: dict) -> list[str]:
    """`.nvmrc` is the one place the Node version is written down.

    CONTRIBUTING says so, both package.json files declare the same range under
    `engines`, and the frontend Dockerfile builds on the matching image -- and
    one job disagreed. `host-pack-macos` said `node-version: 22` while the
    release job that builds the very same pack read `.nvmrc`, so CI's copy of
    the artifact was assembled by an interpreter the packages do not support,
    from a tool-cache build that links `@rpath/libnode.127.dylib` where 24.x is
    a single static executable. The pack's Node could not start, and main was
    red for three days on a job nothing was watching.

    Cheap to write down, so it is written down: a literal `node-version:` in a
    workflow is the drift, whatever it happens to be set to today.
    """
    problems = []
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if "node-version" in (step.get("with") or {}):
                problems.append(
                    f"{workflow}: job '{name}' pins node-version literally. Use "
                    f"`node-version-file: .nvmrc`, which every other Node job "
                    f"and every release workflow reads."
                )
    return problems


def unwatched_workflows() -> list[str]:
    """Every workflow must be named in the failure notifier.

    `workflow_run` takes a literal list of workflow names -- there is no
    wildcard -- so the notifier is only as complete as a list somebody has to
    remember to edit. Nobody would, and the cost of not doing it is exactly
    what this repository already paid: the nightly product scenarios red 9 runs
    running, the weekly security scan red for six weeks, and a host-pack job
    red through eleven merges, all of them reporting into a run list.

    Matched on the workflow's `name:`, which is what `workflow_run` matches on
    -- not the filename, which it ignores.
    """
    notifier = yaml.safe_load((WORKFLOWS / NOTIFIER).read_text())
    triggers = notifier.get(True) or notifier.get("on") or {}
    watched = set((triggers.get("workflow_run") or {}).get("workflows") or [])

    problems = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name == NOTIFIER or path.name in UNWATCHED:
            continue
        name = yaml.safe_load(path.read_text()).get("name")
        if name not in watched:
            problems.append(
                f"{path.name}: workflow '{name}' is not watched by {NOTIFIER}, so "
                f"a failure outside a pull request is announced nowhere. Add it "
                f"to that file's `on.workflow_run.workflows` list."
            )
    return problems


def main() -> int:
    problems: list[str] = []

    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = yaml.safe_load(path.read_text())
        jobs = document.get("jobs") or {}
        problems.extend(literal_node_versions(path.name, jobs))

        if path.name not in NO_TIMEOUT_REQUIRED:
            for name, job in jobs.items():
                if "timeout-minutes" not in job:
                    problems.append(
                        f"{path.name}: job '{name}' has no timeout-minutes, so it "
                        f"runs to GitHub's 6-hour default"
                    )

        problems.extend(unknown_change_outputs(path.name, jobs))

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

    problems.extend(unwatched_workflows())

    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print(f"Aggregator and timeout checks passed "
          f"({len(list(WORKFLOWS.glob('*.yml')))} workflows).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
