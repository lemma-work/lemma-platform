"""The runtime half of the silent-skip gate.

`check_pytest_census.py` sees collection; this sees the run. The gap between
them is a test that is collected, selected, started, and then skips itself from
a fixture -- which is how 72 workspace integration tests behave when Postgres is
unreachable, and how the same suite once skipped 57 tests while CI stayed green.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_test_run import check, skipped_names, summarise  # noqa: E402

BASELINE = {"unit": {"tests": 100, "skipped": 4}}


def junit(tmp_path: Path, tests: int, skipped: int, names: list[str] | None = None) -> Path:
    cases = "".join(
        f'<testcase classname="m" name="{name}"><skipped/></testcase>'
        for name in (names or [f"s{index}" for index in range(skipped)])
    )
    report = tmp_path / "junit.xml"
    report.write_text(
        f'<testsuite name="pytest" tests="{tests}" skipped="{skipped}" '
        f'failures="0" errors="0">{cases}</testsuite>'
    )
    return report


def test_more_runtime_skips_than_recorded_is_a_failure(tmp_path: Path) -> None:
    problems = check("unit", summarise(junit(tmp_path, 100, 5)), BASELINE)
    assert len(problems) == 1
    assert "skipped themselves at runtime, up from 4" in problems[0]


def test_the_recorded_number_of_skips_passes(tmp_path: Path) -> None:
    assert check("unit", summarise(junit(tmp_path, 100, 4)), BASELINE) == []


def test_removing_a_skip_never_fails(tmp_path: Path) -> None:
    # A ceiling, not a floor. Fixing a skip must not be a chore.
    assert check("unit", summarise(junit(tmp_path, 100, 0)), BASELINE) == []


def test_a_lane_that_lost_tests_is_reported(tmp_path: Path) -> None:
    problems = check("unit", summarise(junit(tmp_path, 80, 0)), BASELINE)
    assert any("down from 100" in problem for problem in problems)


def test_the_scenario_this_exists_for(tmp_path: Path) -> None:
    """Postgres does not come up, so a whole suite skips and the lane is green.

    The count is what gives it away: nothing about collection changed, every
    test still exists, and pytest reports success.
    """
    problems = check("unit", summarise(junit(tmp_path, 100, 76)), BASELINE)
    assert len(problems) == 1
    assert "76 tests skipped" in problems[0]


def test_an_unrecorded_lane_is_refused_rather_than_assumed_fine(tmp_path: Path) -> None:
    problems = check("e2e", summarise(junit(tmp_path, 10, 0)), BASELINE)
    assert len(problems) == 1
    assert "no ceiling recorded" in problems[0]


def test_totals_are_summed_across_suites(tmp_path: Path) -> None:
    # pytest-xdist and nested <testsuites> produce more than one suite, and
    # reading only the first would under-count every sharded lane.
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites>'
        '<testsuite name="a" tests="10" skipped="1" failures="0" errors="0"/>'
        '<testsuite name="b" tests="7" skipped="2" failures="0" errors="0"/>'
        '</testsuites>'
    )
    assert summarise(report) == {
        "tests": 17,
        "skipped": 3,
        "failures": 0,
        "errors": 0,
    }


def test_the_skipped_tests_are_named(tmp_path: Path) -> None:
    # "12 more skipped" without the names is a puzzle, not a report.
    report = junit(tmp_path, 3, 2, names=["test_alpha", "test_beta"])
    assert skipped_names(report) == ["m::test_alpha", "m::test_beta"]


def test_a_report_with_no_suite_is_an_error_not_a_pass(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text("<nothing/>")
    with pytest.raises(SystemExit):
        summarise(report)


def test_the_committed_baseline_parses_and_covers_the_unit_lane() -> None:
    baseline = json.loads((REPO_ROOT / ".github" / "test-run-baseline.json").read_text())
    assert "unit" in baseline, "the lane CI gates on must have a ceiling"
    assert isinstance(baseline["unit"]["skipped"], int)
    assert isinstance(baseline["unit"]["tests"], int)
