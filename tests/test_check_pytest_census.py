"""The census that makes a suite's disappearance loud.

The collection pass itself takes ~40 s and imports the whole backend, so it is
not run here. Everything below exercises the decision made *from* a census, and
the last two tests exercise the two things read off disk -- the Makefile's
marker expression and pytest.ini's marker list -- because both are places a
copy could drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_pytest_census import (  # noqa: E402
    CEILING_MARKERS,
    ENVIRONMENT_GATED,
    check,
    lanes,
    registered_markers,
    selects,
)

# These censuses are two markers wide, so every *other* marker pytest.ini
# registers is legitimately absent from them. Recording that here keeps the
# "registered but nothing carries it" arm from drowning out what each test is
# actually about; `test_a_registered_marker_nothing_carries_is_reported` turns
# it back on.
BASELINE = {
    "collected": 100,
    "markers": {"unit": 50, "e2e": 40, "skip": 2},
    "empty_markers": [name for name in registered_markers() if name not in {"unit", "e2e"}],
}


def census(collected: int = 100, **markers: list[str]) -> dict:
    return {"_collected": [str(collected)], **markers}


def test_a_marker_losing_tests_is_a_failure():
    problems = check(
        census(unit=["a"] * 49, e2e=["b"] * 40, skip=["c", "d"]), BASELINE
    )
    assert any("'unit' collects 49" in problem for problem in problems)


def test_a_marker_gaining_tests_is_fine():
    problems = check(
        census(120, unit=["a"] * 70, e2e=["b"] * 40, skip=["c", "d"]), BASELINE
    )
    assert problems == []


def test_skips_are_a_ceiling_not_a_floor():
    # Adding an unconditional skip has to be recorded; removing one never fails.
    grew = check(
        census(unit=["a"] * 50, e2e=["b"] * 40, skip=["c", "d", "e"]), BASELINE
    )
    assert any("marked 'skip', up from 2" in problem for problem in grew)
    assert check(census(unit=["a"] * 50, e2e=["b"] * 40, skip=["c"]), BASELINE) == []


def test_the_total_falling_is_reported_on_its_own():
    """The `testpaths` bug: a whole directory stops being collected.

    Every marker can still meet its floor while the total drops, if the lost
    directory's tests carried no marker of their own -- which is the common
    case for plain unit tests.
    """
    problems = check(
        census(60, unit=["a"] * 50, e2e=["b"] * 40, skip=["c", "d"]), BASELINE
    )
    assert any("down from 100" in problem for problem in problems)


def test_an_empty_collection_says_so_before_anything_else():
    problems = check(census(0), BASELINE)
    assert problems == ["collection found no tests at all"]


def test_a_registered_marker_nothing_carries_is_reported():
    problems = check(
        census(unit=["a"] * 50, e2e=["b"] * 40, skip=["c", "d"]),
        {**BASELINE, "empty_markers": []},
    )
    # pytest.ini registers many markers; every one absent from this census and
    # not recorded as legitimately empty should be named.
    assert any("registered but no test carries it" in p for p in problems)


def test_marker_expressions_are_evaluated_not_pattern_matched():
    assert selects("not e2e and not local_guest", {"unit"})
    assert not selects("not e2e and not local_guest", {"local_guest", "integration"})
    assert not selects("not e2e and not local_guest", {"e2e"})
    assert selects("e2e and not slow", {"e2e"})
    assert not selects("e2e and not slow", {"e2e", "slow"})


def test_marker_expressions_are_never_evaluated_as_arbitrary_python():
    with pytest.raises(ValueError):
        selects("unit and __import__('os').listdir('/')", {"unit"})


def test_the_unit_lane_is_read_from_the_makefile_and_excludes_the_gated_suites():
    """The check that would have caught the drift it was written for.

    `test-backend-unit` and `coverage-backend-unit` had different `-m`
    expressions: only the CI one excluded `local_guest`. They share one variable
    now, and this asserts that variable still names the suites that skip green.
    """
    expressions = set(lanes().values())
    assert len(expressions) == 1, f"the unit lanes have drifted apart: {expressions}"
    expression = expressions.pop()
    for marker in ("local_guest", "provider"):
        assert not selects(expression, {marker, "integration"}), (
            f"the unit lane selects '{marker}' tests, which can only skip"
        )


def test_pytest_ini_markers_are_parsed_rather_than_listed_here():
    markers = registered_markers()
    assert "local_guest" in markers
    assert "e2e" in markers
    assert len(markers) > 15
    assert not any(":" in name or " " in name for name in markers)


def test_every_environment_gated_marker_is_one_pytest_ini_registers():
    """A typo in that set would silently check nothing.

    `--strict-markers` makes pytest.ini exhaustive, so a name here that is not
    there is a name no test can carry.
    """
    unknown = ENVIRONMENT_GATED - set(registered_markers())
    assert unknown == set(), f"not registered in pytest.ini: {sorted(unknown)}"
    assert CEILING_MARKERS.isdisjoint(ENVIRONMENT_GATED)
