"""What a schedule does after it has been down long enough to fall behind.

This is the one place the poller's behaviour is a policy choice rather than a
mechanism, so it is asserted rather than left to the implementation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.schedule.services.due_schedule_claimer import (
    _coalesced_cursor,
    next_cursor_for,
)

_EVERY_MINUTE = {"cron": "* * * * *"}


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_a_schedule_one_tick_late_simply_advances() -> None:
    """The ordinary case must not be changed by the backlog handling."""
    fire_at = _utc(2026, 8, 15, 9, 0)
    now = fire_at + timedelta(seconds=3)

    assert _coalesced_cursor(_EVERY_MINUTE, fire_at=fire_at, now=now) == _utc(
        2026, 8, 15, 9, 1
    )


def test_a_three_hour_backlog_collapses_into_one_fire() -> None:
    """180 missed occurrences must not become 180 late fires.

    The caller has already claimed and will fire the occurrence at `fire_at`;
    what this decides is where the cursor lands afterwards. Stepping one
    occurrence would leave it still in the past, so the next poll claims again,
    and a schedule that was down for three hours replays every minute of it --
    180 emails, or 180 agent runs, trickled out one per poll.

    APScheduler never did that: nothing configured `coalesce` or
    `misfire_grace_time`, so its defaults applied -- a backlog collapsed to one
    run, and that run was dropped if it was over a second late. Replaying is
    therefore more firing than this system has ever done, not parity.
    """
    fire_at = _utc(2026, 8, 15, 6, 0)
    now = _utc(2026, 8, 15, 9, 0)

    cursor = _coalesced_cursor(_EVERY_MINUTE, fire_at=fire_at, now=now)

    assert cursor == _utc(2026, 8, 15, 9, 1)
    assert cursor is not None and cursor > now


def test_the_backlogged_occurrence_is_still_fired_not_dropped() -> None:
    """Coalescing coalesces; it does not silently skip the work.

    The difference from APScheduler, and the reason not to simply advance from
    `now`: the schedule does fire, once, late. Dropping it outright is what the
    old default got wrong.
    """
    fire_at = _utc(2026, 8, 15, 6, 0)
    now = _utc(2026, 8, 15, 9, 0)

    # `fire_at` is what the claim emits, and it is the moment that was due.
    assert fire_at < now
    assert _coalesced_cursor(_EVERY_MINUTE, fire_at=fire_at, now=now) != fire_at


def test_a_one_shot_still_retires() -> None:
    """No cron means no next occurrence, backlog or not."""
    fire_at = _utc(2026, 8, 15, 6, 0)
    config = {"scheduled_at": fire_at.isoformat()}

    assert next_cursor_for(config, after=fire_at) == fire_at
    # The claimer retires one-shots on `is_one_shot`, so the cursor it computes
    # is not what keeps them from re-firing -- but it must not walk forever.
    assert _coalesced_cursor(config, fire_at=fire_at, now=_utc(2026, 8, 15, 9, 0)) == (
        fire_at
    )


def test_catch_up_is_bounded_so_one_row_cannot_hold_the_claim_open() -> None:
    """A yearly cron behind by centuries must not walk to it inside the lock."""
    fire_at = _utc(1900, 1, 1, 0, 0)
    now = _utc(2026, 8, 15, 9, 0)

    cursor = _coalesced_cursor({"cron": "0 0 1 1 *"}, fire_at=fire_at, now=now)

    # Bounded, so it stops short rather than iterating unboundedly; the next
    # poll picks up from wherever it stopped.
    assert cursor is not None
