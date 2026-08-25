#!/usr/bin/env python3
"""Fail when a job skipped although the filter it gates on said to run.

`check_ci_aggregators.py` is the static half: it proves every job is watched
and that no job gates on an output nobody declares. Both checks read the
workflow file, and neither can see what actually happened on a run.

This is the runtime half, and it closes the last way a required check goes
green because a suite stopped running. A skipped job contributes `skipped` to
`needs.*.result`, the aggregator only fails on `failure` and `cancelled`, and
so a job that should have run but didn't is indistinguishable from a job that
was correctly unaffected. The causes are real and undramatic: a filter that
matched during planning and not at dispatch, a `needs:` edge that made a job
inherit an upstream skip, a concurrency cancellation that landed as a skip.

The rule is the one the workflow already means: if `changes` says an area is
`'true'`, every job gating on that area has to have produced a result.

Usage, from the aggregator job:

    CI_NEEDS_JSON='${{ toJSON(needs) }}' python3 scripts/check_ci_job_results.py

The job -> filter mapping is not restated here. It is read back out of the
workflow's own `if:` expressions, so a job that changes which area it gates on
cannot drift away from this check.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# A gating expression this check understands: some number of
# `needs.changes.outputs.<name> == 'true'` comparisons joined by || and &&,
# optionally parenthesised. Anything else -- a `github.event_name`, a
# `!cancelled()`, a comparison against something other than 'true' -- is
# reported as unverifiable rather than guessed at.
COMPARISON = re.compile(r"needs\.changes\.outputs\.([A-Za-z0-9_-]+)\s*==\s*'true'")
STRUCTURE = re.compile(r"^[\s()]*(?:\|\||&&|[\s()])*$")


class Unevaluable(Exception):
    """The `if:` expression says more than this checker can reason about."""


def evaluate(condition: str, outputs: dict[str, str]) -> bool:
    """Decide whether a job's `if:` wanted the job to run on this run.

    Substitutes each comparison for a Python literal and evaluates the
    remaining boolean skeleton, but only after proving the skeleton is nothing
    but the operators we allow -- so this never evaluates workflow text as
    arbitrary Python.
    """
    skeleton = COMPARISON.sub("@", condition)
    if not STRUCTURE.fullmatch(skeleton.replace("@", "")):
        raise Unevaluable(condition)

    values = [outputs.get(name) == "true" for name in COMPARISON.findall(condition)]
    if not values:
        raise Unevaluable(condition)

    expression = skeleton.replace("||", " or ").replace("&&", " and ")
    for value in values:
        expression = expression.replace("@", str(value), 1)
    try:
        return bool(eval(expression, {"__builtins__": {}}, {}))  # noqa: S307
    except SyntaxError as error:  # pragma: no cover - STRUCTURE should prevent this
        raise Unevaluable(condition) from error


def gating_conditions(document: dict) -> dict[str, str]:
    """Every job that gates on a `changes` output, and the expression it uses."""
    conditions = {}
    for name, job in (document.get("jobs") or {}).items():
        condition = job.get("if")
        if isinstance(condition, str) and "needs.changes.outputs" in condition:
            conditions[name] = condition
    return conditions


def check(document: dict, needs: dict) -> tuple[list[str], list[str]]:
    """Return (failures, notes) for one run's `needs` context."""
    outputs = (needs.get("changes") or {}).get("outputs") or {}
    failures: list[str] = []
    notes: list[str] = []

    for name, condition in sorted(gating_conditions(document).items()):
        context = needs.get(name)
        if context is None:
            # Not in the aggregator's `needs:` list. That is its own bug and
            # check_ci_aggregators.py already fails on it; saying so twice
            # would just make one problem look like two.
            continue
        try:
            wanted = evaluate(condition, outputs)
        except Unevaluable:
            notes.append(
                f"job '{name}' gates on an expression this check cannot "
                f"evaluate, so its skip is not verified: {condition.strip()}"
            )
            continue
        if wanted and context.get("result") == "skipped":
            failures.append(
                f"job '{name}' was skipped, but the filter it gates on said to "
                f"run it ({condition.strip()}). A skipped job counts as a pass "
                f"in this aggregator, so this would have merged as green with a "
                f"suite that never ran"
            )
    return failures, notes


def main() -> int:
    raw = os.environ.get("CI_NEEDS_JSON")
    if not raw:
        print("::error::CI_NEEDS_JSON is not set; pass '${{ toJSON(needs) }}'")
        return 1
    try:
        needs = json.loads(raw)
    except json.JSONDecodeError as error:
        print(f"::error::CI_NEEDS_JSON is not JSON: {error}")
        return 1

    workflow = Path(os.environ.get("CI_WORKFLOW_FILE") or DEFAULT_WORKFLOW)
    document = yaml.safe_load(workflow.read_text())

    failures, notes = check(document, needs)
    for note in notes:
        print(f"::notice::{note}")
    for failure in failures:
        print(f"::error::{failure}")
    if failures:
        return 1
    print(f"Every gated job in {workflow.name} produced a result its filter asked for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
