"""The runtime half of the silent-skip gate.

Every case here is a shape that has actually made a required check green while
a suite did not run, or that would if the checker were written slightly less
carefully. The last two tests exist because the previous version of this idea
had to be thrown away: it kept its own job -> filter table, which is a second
copy of something the workflow already states.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_ci_job_results import Unevaluable, check, evaluate  # noqa: E402

WORKFLOW = {
    "jobs": {
        "changes": {"outputs": {"backend": "", "desktop": "", "frontend": ""}},
        "backend-unit": {"if": "needs.changes.outputs.backend == 'true'"},
        "frontend": {
            "if": "needs.changes.outputs.frontend == 'true' "
            "|| needs.changes.outputs.desktop == 'true'"
        },
        "always-runs": {},
    }
}


def needs_context(outputs: dict[str, str], **results: str) -> dict:
    return {"changes": {"result": "success", "outputs": outputs}, **{
        name: {"result": result} for name, result in results.items()
    }}


def test_a_job_that_skipped_while_its_filter_was_live_is_a_failure():
    failures, _ = check(
        WORKFLOW,
        needs_context({"backend": "true"}, **{"backend-unit": "skipped"}),
    )
    assert len(failures) == 1
    assert "backend-unit" in failures[0]


def test_a_job_correctly_unaffected_is_not_a_failure():
    failures, notes = check(
        WORKFLOW,
        needs_context({"backend": "false"}, **{"backend-unit": "skipped"}),
    )
    assert failures == []
    assert notes == []


def test_a_job_that_ran_is_never_a_failure_whatever_the_filter_said():
    # Running more than the filter demanded costs minutes, not correctness.
    for outputs in ({"backend": "true"}, {"backend": "false"}):
        failures, _ = check(
            WORKFLOW, needs_context(outputs, **{"backend-unit": "success"})
        )
        assert failures == []


def test_a_failure_is_left_to_the_aggregator_rather_than_double_reported():
    failures, _ = check(
        WORKFLOW, needs_context({"backend": "true"}, **{"backend-unit": "failure"})
    )
    assert failures == []


def test_either_arm_of_a_disjunction_keeps_the_job_required():
    # The frontend job runs for a desktop-only change too. A checker that read
    # only the first comparison would let that skip through.
    failures, _ = check(
        WORKFLOW,
        needs_context(
            {"frontend": "false", "desktop": "true"}, **{"frontend": "skipped"}
        ),
    )
    assert len(failures) == 1
    assert "frontend" in failures[0]


def test_an_ungated_job_is_not_examined():
    failures, notes = check(
        WORKFLOW, needs_context({"backend": "true"}, **{"always-runs": "skipped"})
    )
    assert failures == []
    assert notes == []


def test_a_job_missing_from_the_aggregator_is_left_to_the_static_check():
    failures, notes = check(WORKFLOW, needs_context({"backend": "true"}))
    assert failures == []
    assert notes == []


def test_an_expression_beyond_this_checker_is_reported_not_guessed():
    workflow = {
        "jobs": {
            "changes": {"outputs": {"backend": ""}},
            "nightly": {
                "if": "needs.changes.outputs.backend == 'true' "
                "&& github.event_name == 'schedule'"
            },
        }
    }
    failures, notes = check(
        workflow, needs_context({"backend": "true"}, nightly="skipped")
    )
    assert failures == [], "a skip we cannot explain must not be called a bug"
    assert len(notes) == 1
    assert "cannot evaluate" in notes[0]


def test_workflow_text_is_never_evaluated_as_python():
    with pytest.raises(Unevaluable):
        evaluate("needs.changes.outputs.backend == 'true' or __import__('os')", {})


def test_the_real_workflow_is_understood_by_this_checker():
    """Every gated job in ci.yml must be one this check can actually verify.

    Without this the file passes vacuously the moment someone writes an `if:`
    in a shape the evaluator does not handle -- the note is printed, the run is
    green, and the gate quietly covers one job fewer. A genuinely conditional
    job may of course be added; the fix is to teach the evaluator, and this
    test is where that decision surfaces. It already has once: `host-pack-macos`
    gates on `github.event_name` as well as a filter, and the choice was to
    teach the evaluator rather than exempt the job.
    """
    import yaml

    document = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    )
    outputs = dict.fromkeys(
        (document["jobs"]["changes"].get("outputs") or {}), "true"
    )
    every_job = {
        name: {"result": "success"} for name in document["jobs"] if name != "changes"
    }
    _, notes = check(
        document, {"changes": {"outputs": outputs}, **every_job}, "push"
    )
    assert notes == [], "\n".join(notes)


def test_a_merge_only_job_may_skip_on_a_pull_request():
    """And must not, on a push where its filter is live.

    `host-pack-macos` builds a 1 GB pack on a macOS runner and is deliberately
    merge-to-main only. Its skip on a PR is correct; its skip on a push to main
    with a desktop change is a lane that stopped running.
    """
    workflow = {
        "jobs": {
            "changes": {"outputs": {"desktop": ""}},
            "host-pack": {
                "if": "github.event_name == 'push' && "
                "needs.changes.outputs.desktop == 'true'"
            },
        }
    }
    context = needs_context({"desktop": "true"}, **{"host-pack": "skipped"})

    on_a_pull_request, notes = check(workflow, context, "pull_request")
    assert on_a_pull_request == []
    assert notes == [], "the expression is understood, not merely tolerated"

    on_a_merge, _ = check(workflow, context, "push")
    assert len(on_a_merge) == 1
    assert "host-pack" in on_a_merge[0]


def test_without_an_event_name_such_a_job_is_reported_rather_than_assumed():
    workflow = {
        "jobs": {
            "changes": {"outputs": {"desktop": ""}},
            "host-pack": {
                "if": "github.event_name == 'push' && "
                "needs.changes.outputs.desktop == 'true'"
            },
        }
    }
    failures, notes = check(
        workflow, needs_context({"desktop": "true"}, **{"host-pack": "skipped"})
    )
    assert failures == []
    assert len(notes) == 1 and "cannot evaluate" in notes[0]


def test_the_script_runs_as_a_command_and_reports_by_exit_status():
    script = REPO_ROOT / "scripts" / "check_ci_job_results.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        env={
            "CI_NEEDS_JSON": json.dumps(
                needs_context({"desktop": "false"}, desktop="skipped")
            ),
            "PATH": "/usr/bin:/bin",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
