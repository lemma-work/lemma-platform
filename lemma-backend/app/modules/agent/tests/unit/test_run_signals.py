"""Steer and stop, pushed: the run reads the database when told to, or on a
slow backstop -- not before every node and every second."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.services.run_limits import make_stop_checker
from app.modules.agent.services.run_signals import RUN_CONTROL, STEER, STOP, RunSignals

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _signals(run_id, clock: _Clock) -> RunSignals:
    return RunSignals(run_id, backstop_seconds=15.0, clock=clock)


def test_a_run_claims_once_at_start_then_only_when_told_or_overdue() -> None:
    run_id, clock = uuid4(), _Clock()
    signals = _signals(run_id, clock)

    assert signals.steer_due(), "what was queued before it listened"
    assert not signals.steer_due()

    signals.heard({"k": RUN_CONTROL, "run": str(uuid4()), "op": STEER})
    assert not signals.steer_due(), "another run's message"

    signals.heard({"k": RUN_CONTROL, "run": str(run_id), "op": STEER})
    assert signals.steer_due()
    assert not signals.steer_due()

    clock.now += 15.0
    assert signals.steer_due(), "the backstop: a signal may have been lost"


def test_a_listener_that_reconnected_looks_at_both() -> None:
    run_id, clock = uuid4(), _Clock()
    signals = _signals(run_id, clock)
    signals.steer_due()

    signals.missed()

    assert signals.steer_due()
    assert signals.stop_check_due()


@pytest.mark.anyio
async def test_a_signalled_stop_is_true_without_a_read() -> None:
    run_id, clock = uuid4(), _Clock()
    signals = _signals(run_id, clock)

    def _no_database():  # pragma: no cover - must not be called
        raise AssertionError("read the database")

    should_stop = make_stop_checker(
        run_id,
        uow_factory=_no_database,  # type: ignore[arg-type]
        signals=signals,
    )

    assert await should_stop() is False, "not signalled, not overdue: no read"
    signals.heard({"k": RUN_CONTROL, "run": str(run_id), "op": STOP})
    assert await should_stop() is True
    assert await should_stop() is True, "sticky"
