"""The work runtime's retry decisions: three budgets, each charged only by its own cause."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.infrastructure.work.retry import (
    AttemptState,
    Dead,
    Exponential,
    Requeue,
    RetryPolicy,
    after_defer,
    after_failure,
    after_lease_lost,
    after_permanent,
    after_unavailable,
)
from app.core.infrastructure.work.types import Unavailable, UnavailableEvidence

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FRESH = AttemptState(attempts=0, lease_losses=0, first_unavailable_at=None)


def _mid() -> float:
    """A jitter source that lands exactly on the unjittered delay."""
    return 0.5


def _down(detail: str = "refused") -> Unavailable:
    return Unavailable(detail, evidence=UnavailableEvidence.CONNECTION_REFUSED)


def test_backoff_doubles_from_base_and_stops_at_the_cap() -> None:
    backoff = Exponential(
        base=timedelta(seconds=30), cap=timedelta(minutes=5), jitter=0
    )
    delays = [backoff.delay(n, rand=_mid).total_seconds() for n in range(1, 7)]
    assert delays == [30, 60, 120, 240, 300, 300]


def test_backoff_jitter_stays_within_its_band() -> None:
    backoff = Exponential(base=timedelta(seconds=100), jitter=0.2)
    assert backoff.delay(1, rand=lambda: 0.0) == timedelta(seconds=80)
    assert backoff.delay(1, rand=lambda: 1.0) == timedelta(seconds=120)


def test_a_huge_failure_count_does_not_overflow() -> None:
    assert Exponential(jitter=0).delay(10_000, rand=_mid) == timedelta(hours=1)


def test_failure_charges_an_attempt_and_ends_an_unavailability_streak() -> None:
    state = AttemptState(attempts=0, lease_losses=1, first_unavailable_at=NOW)
    decision = after_failure(RetryPolicy(), state, now=NOW, error="boom", rand=_mid)
    assert isinstance(decision, Requeue)
    assert decision.attempts == 1
    assert decision.lease_losses == 1
    assert decision.first_unavailable_at is None
    assert decision.run_at == NOW + timedelta(seconds=30)
    assert decision.error_class == "failure"


def test_the_last_allowed_attempt_goes_dead() -> None:
    state = AttemptState(attempts=2, lease_losses=0, first_unavailable_at=None)
    decision = after_failure(RetryPolicy(max_attempts=3), state, now=NOW, error="boom")
    assert decision == Dead("attempts_exhausted", 3, 0, "boom")


def test_unavailable_charges_nothing_and_starts_the_clock() -> None:
    decision = after_unavailable(RetryPolicy(), FRESH, _down(), now=NOW, rand=_mid)
    assert isinstance(decision, Requeue)
    assert decision.attempts == 0
    assert decision.first_unavailable_at == NOW
    assert decision.error_class == "unavailable:connection_refused"


def test_unavailable_keeps_the_streak_start_and_waits_as_long_as_the_outage() -> None:
    started = NOW - timedelta(minutes=10)
    state = AttemptState(attempts=1, lease_losses=0, first_unavailable_at=started)
    decision = after_unavailable(RetryPolicy(), state, _down(), now=NOW, rand=_mid)
    assert isinstance(decision, Requeue)
    assert decision.first_unavailable_at == started
    assert decision.attempts == 1
    assert decision.run_at == NOW + timedelta(minutes=10)


def test_an_outage_longer_than_the_budget_is_dead() -> None:
    policy = RetryPolicy(unavailable_budget=timedelta(hours=1))
    state = AttemptState(0, 0, NOW - timedelta(hours=2))
    decision = after_unavailable(policy, state, _down(), now=NOW)
    assert isinstance(decision, Dead)
    assert decision.reason == "unavailable_exhausted"


def test_a_named_retry_after_wins_when_longer() -> None:
    exc = Unavailable(
        "429",
        evidence=UnavailableEvidence.RATE_LIMITED,
        retry_after=timedelta(minutes=7),
    )
    decision = after_unavailable(RetryPolicy(), FRESH, exc, now=NOW, rand=_mid)
    assert isinstance(decision, Requeue)
    assert decision.run_at == NOW + timedelta(minutes=7)


@pytest.mark.parametrize(("losses", "dead"), [(0, False), (2, False), (3, True)])
def test_lease_losses_have_their_own_budget(losses: int, dead: bool) -> None:
    state = AttemptState(attempts=2, lease_losses=losses, first_unavailable_at=None)
    decision = after_lease_lost(RetryPolicy(max_lease_losses=3), state, now=NOW)
    assert isinstance(decision, Dead) is dead
    assert decision.attempts == 2
    assert decision.lease_losses == losses + 1


def test_defer_touches_no_counter() -> None:
    state = AttemptState(attempts=1, lease_losses=2, first_unavailable_at=NOW)
    decision = after_defer(state, timedelta(seconds=5), now=NOW)
    assert decision == Requeue(NOW + timedelta(seconds=5), 1, 2, NOW, None, None)


def test_permanent_is_dead_at_once_with_its_reason() -> None:
    decision = after_permanent(FRESH, reason="invalid_payload", error="bad")
    assert decision == Dead("invalid_payload", 1, 0, "bad")
