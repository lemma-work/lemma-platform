from __future__ import annotations

import pytest

from app.modules.datastore.infrastructure.kreuzberg_circuit import (
    KreuzbergCircuitBreaker,
    KreuzbergCircuitOpen,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(clock, *, threshold=3, reset=30.0):
    return KreuzbergCircuitBreaker(
        failure_threshold=threshold, reset_seconds=reset, clock=clock
    )


def test_closed_circuit_does_not_raise():
    breaker = _breaker(_FakeClock())
    breaker.raise_if_open()  # no failures yet
    assert breaker.is_open is False


def test_opens_after_threshold_consecutive_failures():
    clock = _FakeClock()
    breaker = _breaker(clock, threshold=3)

    breaker.record_failure()
    breaker.record_failure()
    breaker.raise_if_open()  # 2 < 3, still closed
    assert breaker.is_open is False

    breaker.record_failure()  # 3rd → opens
    assert breaker.is_open is True
    with pytest.raises(KreuzbergCircuitOpen):
        breaker.raise_if_open()


def test_success_resets_failure_streak():
    breaker = _breaker(_FakeClock(), threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()  # streak reset
    breaker.record_failure()
    breaker.record_failure()
    breaker.raise_if_open()  # only 2 consecutive since reset → still closed
    assert breaker.is_open is False


def test_stays_open_during_cooldown_then_half_opens():
    clock = _FakeClock()
    breaker = _breaker(clock, threshold=1, reset=30.0)

    breaker.record_failure()  # opens immediately (threshold 1)
    assert breaker.is_open is True

    clock.advance(10)
    with pytest.raises(KreuzbergCircuitOpen):
        breaker.raise_if_open()  # within cooldown

    clock.advance(25)  # total 35 > 30 → half-open trial allowed
    breaker.raise_if_open()  # does not raise


def test_half_open_success_closes_circuit():
    clock = _FakeClock()
    breaker = _breaker(clock, threshold=1, reset=30.0)
    breaker.record_failure()
    clock.advance(31)
    breaker.raise_if_open()  # half-open trial
    breaker.record_success()  # trial succeeded
    assert breaker.is_open is False
    breaker.raise_if_open()


def test_half_open_failure_reopens_and_rearms_cooldown():
    clock = _FakeClock()
    breaker = _breaker(clock, threshold=1, reset=30.0)
    breaker.record_failure()
    clock.advance(31)
    breaker.raise_if_open()  # half-open trial
    breaker.record_failure()  # trial failed → re-arm cooldown from now
    assert breaker.is_open is True
    with pytest.raises(KreuzbergCircuitOpen):
        breaker.raise_if_open()  # cooldown restarted
