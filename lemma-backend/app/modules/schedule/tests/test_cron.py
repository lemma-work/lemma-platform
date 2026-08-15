"""Cron parsing, held to the behaviour APScheduler's CronTrigger had.

This replaced `CronTrigger` so the scheduler package could be deleted, and cron
is the part of scheduling where a subtle difference is invisible until a
schedule fires at the wrong time for a week. So the equivalence is asserted
rather than assumed: the same expressions, the same instants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.schedule.domain.cron import CronSchedule

# Expressions that pin no day-of-week. On these the replacement must agree with
# APScheduler exactly -- any difference here is a regression, not a decision.
_DAY_OF_WEEK_FREE = [
    "* * * * *",
    "*/5 * * * *",
    "0 * * * *",
    "30 2 1 * *",
    "0 0 1 1 *",
    "5,10,15 * * * *",
    "0 0 29 2 *",
]

# The one deliberate difference, and what it now means.
# APScheduler's `from_crontab` maps day-of-week as 0=Monday. Standard cron --
# every crontab(5) man page, and what anyone writing an expression expects -- is
# 0=Sunday. So `0 9 * * 1` fired on TUESDAY under APScheduler and fires on
# MONDAY now, and `1-5` meant Tue-Sat and now means Mon-Fri.
#
# This ships without a data migration by decision, so existing day-of-week
# schedules move by one day when it deploys. That is the point of the table:
# the shift is intended, recorded, and asserted, rather than discovered.
_DAY_OF_WEEK_CASES = [
    ("0 9 * * 0", "Sunday"),
    ("0 9 * * 1", "Monday"),
    ("0 9 * * 5", "Friday"),
    ("0 9 * * 6", "Saturday"),
]


@pytest.mark.parametrize("expression", _DAY_OF_WEEK_FREE)
def test_matches_apscheduler_where_no_day_of_week_is_pinned(expression: str) -> None:
    """Everything except day-of-week must be instant-for-instant identical.

    Cron is where a subtle difference is invisible until a schedule fires at the
    wrong time for a week, so the equivalence is asserted rather than assumed.

    Skips once APScheduler is gone -- at that point this has done its job and
    the behavioural tests below stand on their own.
    """
    apscheduler_cron = pytest.importorskip("apscheduler.triggers.cron")
    reference = apscheduler_cron.CronTrigger.from_crontab(
        expression, timezone=timezone.utc
    )
    schedule = CronSchedule.parse(expression)

    # Deliberately off a boundary. `get_next_fire_time(None, now)` is inclusive
    # of `now`, while a poller asks "what fires strictly after the last one" --
    # comparing those at the first iteration measures the difference between two
    # questions rather than two implementations.
    cursor = datetime(2026, 3, 1, 0, 0, 31, tzinfo=timezone.utc)
    previous = cursor
    for _ in range(25):
        expected = reference.get_next_fire_time(previous, cursor)
        actual = schedule.next_fire_time(cursor)
        if actual is None:
            # Past `crontab`'s lookahead horizon (a century out for `29 2`).
            # Not a disagreement about scheduling, so stop comparing.
            break
        assert actual == expected, (
            f"{expression}: after {cursor.isoformat()} expected "
            f"{expected.isoformat()} got {actual.isoformat()}"
        )
        previous, cursor = expected, expected


@pytest.mark.parametrize(("expression", "weekday"), _DAY_OF_WEEK_CASES)
def test_day_of_week_follows_standard_cron(expression: str, weekday: str) -> None:
    """0=Sunday, as crontab(5) defines it -- not APScheduler's 0=Monday."""
    schedule = CronSchedule.parse(expression)
    start = datetime(2026, 3, 1, 0, 0, 31, tzinfo=timezone.utc)
    fire = schedule.next_fire_time(start)
    assert fire is not None
    assert fire.strftime("%A") == weekday, (
        f"{expression} fired on {fire.strftime('%A')} {fire.date()}, expected {weekday}"
    )


def test_weekday_range_means_monday_to_friday() -> None:
    """`1-5` is the canonical "weekdays" expression.

    Under APScheduler it produced Tuesday through Saturday, which is the bug
    this replacement fixes and the reason existing schedules shift a day.
    """
    schedule = CronSchedule.parse("0 22 * * 1-5")
    cursor = datetime(2026, 3, 1, tzinfo=timezone.utc)
    days = []
    for _ in range(5):
        cursor = schedule.next_fire_time(cursor)
        assert cursor is not None
        days.append(cursor.strftime("%A"))
    assert days == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


@pytest.mark.parametrize("expression", ["", "   ", "* * * *", "* * * * * *", "nope"])
def test_invalid_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(ValueError):
        CronSchedule.parse(expression)


def test_six_fields_is_rejected_not_read_as_seconds() -> None:
    """`crontab` accepts a six-field form whose first field is seconds.

    Silently accepting it would turn `*/30 * * * * *` into a schedule that
    fires twice a minute, which is not a mistake anyone would catch by reading
    the row back.
    """
    with pytest.raises(ValueError):
        CronSchedule.parse("*/30 * * * * *")


def test_next_fire_time_is_strictly_after_the_moment_given() -> None:
    """Otherwise a poller that passes the last fire time gets it again forever."""
    schedule = CronSchedule.parse("0 * * * *")
    on_the_hour = datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)
    assert schedule.next_fire_time(on_the_hour) == on_the_hour + timedelta(hours=1)


def test_a_naive_datetime_is_read_as_utc() -> None:
    """Schedules are defined in UTC; the host's local zone must not leak in."""
    schedule = CronSchedule.parse("0 * * * *")
    naive = datetime(2026, 3, 1, 14, 30)
    aware = naive.replace(tzinfo=timezone.utc)
    assert schedule.next_fire_time(naive) == schedule.next_fire_time(aware)


def test_a_non_utc_datetime_is_converted_not_stripped() -> None:
    schedule = CronSchedule.parse("0 * * * *")
    tokyo = timezone(timedelta(hours=9))
    moment = datetime(2026, 3, 1, 23, 30, tzinfo=tokyo)
    assert schedule.next_fire_time(moment) == datetime(
        2026, 3, 1, 15, 0, tzinfo=timezone.utc
    )


def test_the_expression_survives_parsing() -> None:
    """It is stored on schedule rows, so it has to round-trip unchanged."""
    schedule = CronSchedule.parse("  */5 * * * *  ")
    assert schedule.expression == "*/5 * * * *"


def test_fire_times_are_ordered_and_bounded_by_the_limit() -> None:
    schedule = CronSchedule.parse("*/5 * * * *")
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    times = list(schedule.fire_times_from(start, limit=10))
    assert len(times) == 10
    assert times == sorted(times)
    assert all(later > earlier for earlier, later in zip(times, times[1:]))
