"""Waiting on a child must not cost a pooled connection per second.

``SubAgentService.await_run`` and the JOB-function tool both open a fresh unit
of work every tick. At a fixed interval, one five-minute wait is hundreds of
checkouts of a pool holding ten plus ten overflow per process -- and the cost
scales with exactly the delegation the sub-agent feature exists to enable.
"""

from __future__ import annotations

from app.modules.agent.services.poll_backoff import (
    POLL_BACKOFF_CAP_SECONDS,
    poll_delay,
)

_BASE = 1.0
_UNBOUNDED = 10_000.0


def _delays(count: int, *, base_seconds: float = _BASE) -> list[float]:
    return [
        poll_delay(attempt, base_seconds=base_seconds, remaining_seconds=_UNBOUNDED)
        for attempt in range(1, count + 1)
    ]


def test_the_opening_checks_keep_the_callers_own_interval() -> None:
    """A child that finishes quickly is the common case, and someone is
    watching -- so backing off immediately would trade latency for nothing."""
    assert _delays(5) == [_BASE] * 5


def test_a_long_wait_settles_to_the_cap() -> None:
    delays = _delays(30)

    assert delays[-1] == POLL_BACKOFF_CAP_SECONDS
    assert delays[5] > _BASE


def test_a_full_length_wait_costs_a_fraction_of_the_checkouts() -> None:
    """The whole point: the same wait, far fewer units of work opened.

    Bounded against the fixed-interval cost it replaces (one check per
    ``base_seconds``) rather than an absolute number, so the assertion still
    means something if the cap moves.
    """
    budget = 300.0
    elapsed = 0.0
    checks = 0
    while elapsed < budget:
        checks += 1
        elapsed += poll_delay(
            checks, base_seconds=_BASE, remaining_seconds=budget - elapsed
        )

    fixed_interval_checks = budget / _BASE
    assert checks < fixed_interval_checks / 4


def test_a_pause_never_runs_past_the_deadline() -> None:
    """The deadline is checked before the sleep, so an unclamped pause would
    overrun the caller's own timeout by as much as the cap."""
    assert poll_delay(50, base_seconds=_BASE, remaining_seconds=0.4) == 0.4
    assert poll_delay(50, base_seconds=_BASE, remaining_seconds=-2.0) == 0.0


def test_a_sub_second_base_interval_still_backs_off() -> None:
    """The function tool polls at 0.5s by default, which was the worse of the
    two: 600 checkouts for one wait."""
    delays = _delays(30, base_seconds=0.5)

    assert delays[:5] == [0.5] * 5
    assert delays[-1] == POLL_BACKOFF_CAP_SECONDS
