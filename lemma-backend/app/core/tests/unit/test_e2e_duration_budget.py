"""The duration budget must judge a test on its own work, not the shard's.

pytest charges session-scoped fixture setup to whichever test happens to
trigger it first. On an e2e shard that is testcontainers, the schema build and
a worker subprocess: twenty-five to thirty-five seconds of one-time
infrastructure, varying by eight run to run, landing on one arbitrary test.

That is what failed PR #518. `test_public_sse_sanitizes_provider_failure_matrix`
was measured at 45.3s against a 45s budget, of which 31.5s was setup it merely
went first for. Its own body was 13.8s, and the same test had passed at 37.8s
an hour earlier on another branch.

The gate was wrong twice over there. It fired on collection order rather than
on anything slow, and every remedy it offered was useless against the actual
cost: `@pytest.mark.slow` moves the test, and the session setup lands on
whichever test is next. So the carrier is judged on call plus teardown, read
from the phase breakdown the root conftest writes beside the JUnit.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]

pytestmark = pytest.mark.unit

CARRIER = "app.modules.agent.tests.e2e.test_journeys::test_the_one_that_went_first"
ORDINARY = "app.modules.agent.tests.e2e.test_journeys::test_an_ordinary_one"


def _load_durations():
    """The shipped script, imported rather than reimplemented."""
    spec = importlib.util.spec_from_file_location(
        "e2e_durations", _REPO_ROOT / "scripts/e2e_durations.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(seconds: float, test: str) -> tuple[float, str, str]:
    classname, _, name = test.partition("::")
    return (seconds, classname, name)


def test_the_test_that_paid_for_the_session_is_judged_on_its_own_work() -> None:
    """The real numbers from the run that failed: 31.5s setup, 13.8s call."""
    durations = _load_durations()

    own = dict(
        (test, seconds)
        for seconds, test in durations._own_seconds(
            [_case(45.29, CARRIER)],
            {
                "session_setup_carriers": [CARRIER],
                "phases": {CARRIER: {"setup": 31.50, "call": 13.79, "teardown": 0.01}},
            },
        )
    )

    assert own[CARRIER] == pytest.approx(13.80, abs=0.01), (
        "the carrier was judged on the JUnit total, which includes the shard's "
        "one-time session setup"
    )


def test_a_test_that_is_genuinely_slow_is_still_caught() -> None:
    """The guard above must not become a way through the gate."""
    durations = _load_durations()

    exit_code = durations.check(
        [_case(70.0, CARRIER)],
        45.0,
        {
            "session_setup_carriers": [CARRIER],
            # Slow in the body, not in the setup it happened to trigger.
            "phases": {CARRIER: {"setup": 4.0, "call": 66.0, "teardown": 0.0}},
        },
    )

    assert exit_code == 1


def test_every_other_test_is_still_judged_on_the_full_total() -> None:
    """Only the carrier is special. A non-carrier keeps its own setup."""
    durations = _load_durations()

    own = dict(
        (test, seconds)
        for seconds, test in durations._own_seconds(
            [_case(50.0, ORDINARY)],
            {
                "session_setup_carriers": [CARRIER],
                "phases": {ORDINARY: {"setup": 40.0, "call": 10.0, "teardown": 0.0}},
            },
        )
    )

    assert own[ORDINARY] == 50.0


def test_a_run_with_no_phase_breakdown_behaves_as_it_did_before() -> None:
    """An older artifact still checks, against the JUnit total."""
    durations = _load_durations()

    own = dict(
        (test, seconds)
        for seconds, test in durations._own_seconds([_case(45.29, CARRIER)], {})
    )

    assert own[CARRIER] == 45.29
