"""Retry policy as data, and the pure decisions made from it.

Three budgets, kept apart on purpose, because each failure has a different
culprit:

* ``attempts`` -- failures this item caused. Charged by any exception that is not
  ``Unavailable``, ``Defer`` or ``Released``.
* ``lease_losses`` -- the process died or stalled while running it. One OOM
  charges every item that was in flight, so it cannot share the attempts budget.
* ``unavailable_budget`` -- how long "the dependency is down" may last, measured
  from the first such failure of the current streak. Time, not a count: a
  dependency that is down for an hour is the same outage whether we asked it
  twice or two hundred times.

Every function here is pure: the clock and the jitter source are arguments.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.infrastructure.work.types import Unavailable

#: Longest ``last_error`` stored on a row. The full traceback goes to the log.
MAX_ERROR_CHARS = 2000


@dataclass(frozen=True, slots=True)
class Exponential:
    base: timedelta = timedelta(seconds=30)
    cap: timedelta = timedelta(hours=1)
    jitter: float = 0.2

    def delay(self, failures: int, *, rand: Callable[[], float]) -> timedelta:
        """Backoff after the ``failures``-th failure (1-based), jittered ±``jitter``."""
        exponent = max(failures - 1, 0)
        # Capped before the power, so a large count cannot overflow a float.
        raw = self.base.total_seconds() * (2 ** min(exponent, 32))
        return _jittered(min(raw, self.cap.total_seconds()), self.jitter, rand)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    max_lease_losses: int = 3
    unavailable_budget: timedelta = timedelta(hours=24)
    backoff: Exponential = field(default_factory=Exponential)


DEFAULT_RETRY = RetryPolicy()


@dataclass(frozen=True, slots=True)
class AttemptState:
    """The counters a decision reads, as they are on the row now."""

    attempts: int
    lease_losses: int
    first_unavailable_at: datetime | None


@dataclass(frozen=True, slots=True)
class Requeue:
    run_at: datetime
    attempts: int
    lease_losses: int
    first_unavailable_at: datetime | None
    error_class: str | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class Dead:
    """Terminal. ``reason`` is stored as the row's ``error_class``."""

    reason: str
    attempts: int
    lease_losses: int
    last_error: str | None


type Decision = Requeue | Dead


def _jittered(seconds: float, jitter: float, rand: Callable[[], float]) -> timedelta:
    spread = seconds * jitter
    return timedelta(seconds=max(0.0, seconds + (rand() * 2 - 1) * spread))


def describe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:MAX_ERROR_CHARS]


def after_failure(
    policy: RetryPolicy,
    state: AttemptState,
    *,
    now: datetime,
    error: str,
    error_class: str = "failure",
    rand: Callable[[], float] = random.random,
) -> Decision:
    """The item failed: charge an attempt, and end the unavailability streak."""
    attempts = state.attempts + 1
    if attempts >= policy.max_attempts:
        return Dead("attempts_exhausted", attempts, state.lease_losses, error)
    return Requeue(
        run_at=now + policy.backoff.delay(attempts, rand=rand),
        attempts=attempts,
        lease_losses=state.lease_losses,
        first_unavailable_at=None,
        error_class=error_class,
        last_error=error,
    )


def after_unavailable(
    policy: RetryPolicy,
    state: AttemptState,
    exc: Unavailable,
    *,
    now: datetime,
    rand: Callable[[], float] = random.random,
) -> Decision:
    """A dependency is down: no attempt charged, but the outage is on the clock.

    The wait grows with the outage itself -- as long as it has already lasted,
    within the backoff's base and cap -- which doubles it without storing a
    count. A ``retry_after`` the dependency named wins when it is longer.
    """
    error = describe_error(exc)
    started = state.first_unavailable_at or now
    if now - started > policy.unavailable_budget:
        return Dead("unavailable_exhausted", state.attempts, state.lease_losses, error)
    backoff = policy.backoff
    seconds = min(
        max((now - started).total_seconds(), backoff.base.total_seconds()),
        backoff.cap.total_seconds(),
    )
    delay = _jittered(seconds, backoff.jitter, rand)
    if exc.retry_after is not None and exc.retry_after > delay:
        delay = exc.retry_after
    return Requeue(
        run_at=now + delay,
        attempts=state.attempts,
        lease_losses=state.lease_losses,
        first_unavailable_at=started,
        error_class=f"unavailable:{exc.evidence.value}",
        last_error=error,
    )


def after_lease_lost(
    policy: RetryPolicy,
    state: AttemptState,
    *,
    now: datetime,
    rand: Callable[[], float] = random.random,
) -> Decision:
    """The lease expired under a dead or stalled process: charge a lease loss."""
    losses = state.lease_losses + 1
    if losses > policy.max_lease_losses:
        return Dead("lease_losses_exhausted", state.attempts, losses, "lease expired")
    return Requeue(
        run_at=now + policy.backoff.delay(losses, rand=rand),
        attempts=state.attempts,
        lease_losses=losses,
        first_unavailable_at=state.first_unavailable_at,
        error_class="lease_lost",
        last_error="lease expired",
    )


def after_permanent(state: AttemptState, *, reason: str, error: str) -> Decision:
    return Dead(reason, state.attempts + 1, state.lease_losses, error)


def after_defer(state: AttemptState, delay: timedelta, *, now: datetime) -> Decision:
    """Not now: back to the queue with every counter untouched."""
    return Requeue(
        run_at=now + delay,
        attempts=state.attempts,
        lease_losses=state.lease_losses,
        first_unavailable_at=state.first_unavailable_at,
        error_class=None,
        last_error=None,
    )
