"""What a swept run tells the person reading it.

Two different failures used to read almost identically in a run list. The
dispatcher's own inline timeout says "Function execution timed out (deadline
exceeded)"; the once-a-minute sweep said "Function execution deadline exceeded".
Their remedies are opposite — make the function faster, versus find out why the
runtime never reported — and a reader could not tell which kind they had.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.function.domain.run_deadline import expired_run_error

pytestmark = pytest.mark.unit


def _run(*, budget_seconds: int, started_ago: int):
    started = datetime.now(timezone.utc) - timedelta(seconds=started_ago)
    return SimpleNamespace(
        started_at=started,
        deadline_at=started + timedelta(seconds=budget_seconds),
    )


def _expired_run_error(run, *, now: datetime) -> str:
    return expired_run_error(
        started_at=run.started_at, deadline_at=run.deadline_at, now=now
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


def test_it_says_the_platform_ended_it_not_the_function() -> None:
    message = _expired_run_error(
        _run(budget_seconds=600, started_ago=700), now=datetime.now(timezone.utc)
    )

    assert "ended by the platform" in message
    assert "never reported" in message


def test_it_is_distinguishable_from_the_dispatchers_inline_timeout() -> None:
    """The string the dispatcher produces for its own timeout, which this one
    must not collide with."""
    swept = _expired_run_error(
        _run(budget_seconds=120, started_ago=130), now=datetime.now(timezone.utc)
    )

    assert swept != "Function execution timed out (deadline exceeded)"
    assert swept != "Function execution deadline exceeded"


def test_a_run_that_never_started_says_so_and_that_retrying_is_safe() -> None:
    """A PENDING run swept at its deadline did nothing; its deadline was only
    when it had to start by, so it is not an execution timeout."""
    message = _expired_run_error(
        SimpleNamespace(
            started_at=None,
            deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ),
        now=datetime.now(timezone.utc),
    )

    assert message.startswith("Function never started")
    assert "safe to retry" in message
