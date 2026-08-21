"""What a swept run tells the person reading it.

Two different failures used to read almost identically in a run list. The
dispatcher's own inline timeout says "Function execution timed out (deadline
exceeded)"; the once-a-minute sweep said "Function execution deadline exceeded".
Their remedies are opposite — make the function faster, versus find out why the
runtime never reported — and 41 failures in one afternoon left the reader to
guess which kind they had.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.function.infrastructure.repositories import _expired_run_error

pytestmark = pytest.mark.unit


def _run(*, budget_seconds: int, started_ago: int):
    started = datetime.now(timezone.utc) - timedelta(seconds=started_ago)
    return SimpleNamespace(
        started_at=started,
        deadline_at=started + timedelta(seconds=budget_seconds),
    )


def test_it_states_the_budget_because_nothing_else_does() -> None:
    """The budget comes from a deployment-wide setting keyed on function type,
    so a reader looking at the failed run cannot otherwise tell whether it was
    given two minutes or ten."""
    message = _expired_run_error(
        _run(budget_seconds=600, started_ago=660), now=datetime.now(timezone.utc)
    )

    assert "600s budget" in message
    assert "660s" in message


def test_it_says_the_sweep_ended_it_not_the_function() -> None:
    message = _expired_run_error(
        _run(budget_seconds=600, started_ago=700), now=datetime.now(timezone.utc)
    )

    assert "sweep" in message
    assert "never reported" in message


def test_it_is_distinguishable_from_the_dispatchers_inline_timeout() -> None:
    """The string the dispatcher produces for its own timeout, which this one
    must not collide with."""
    swept = _expired_run_error(
        _run(budget_seconds=120, started_ago=130), now=datetime.now(timezone.utc)
    )

    assert swept != "Function execution timed out (deadline exceeded)"
    assert swept != "Function execution deadline exceeded"


def test_a_run_with_no_timestamps_still_gets_a_usable_reason() -> None:
    message = _expired_run_error(
        SimpleNamespace(started_at=None, deadline_at=None),
        now=datetime.now(timezone.utc),
    )

    assert "never reported" in message
