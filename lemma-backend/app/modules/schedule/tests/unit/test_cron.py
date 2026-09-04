"""Cron parsing, held to the behaviour APScheduler's CronTrigger had.

This replaced `CronTrigger` so the scheduler package could be deleted, and cron
is the part of scheduling where a subtle difference is invisible until a
schedule fires at the wrong time for a week. So the equivalence is asserted
rather than assumed: the same expressions, the same instants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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


# --- Time zones -------------------------------------------------------------
#
# `0 9 * * *` means nine in the morning where the person who wrote it lives.
# Everything below pins the instants that produces, because a zone bug is
# invisible for months: the schedule fires, just an hour off, and only twice a
# year does anything change.

_NEW_YORK = "America/New_York"
# 2026 US transitions, the two dates every case below is built on.
_SPRING_FORWARD = "2026-03-08"  # 02:00 EST -> 03:00 EDT, so 02:00 does not exist
_FALL_BACK = "2026-11-01"  # 02:00 EDT -> 01:00 EST, so 01:00 happens twice


def test_a_zoned_cron_fires_at_the_local_wall_clock_not_the_utc_one() -> None:
    """The whole point: 09:00 Berlin is 07:00Z in winter and 08:00Z in summer."""
    schedule = CronSchedule.parse("0 9 * * *", zone="Europe/Berlin")
    winter = schedule.next_fire_time(datetime(2026, 1, 15, 0, tzinfo=timezone.utc))
    summer = schedule.next_fire_time(datetime(2026, 7, 15, 0, tzinfo=timezone.utc))
    assert winter == datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)
    assert summer == datetime(2026, 7, 15, 7, 0, tzinfo=timezone.utc)


def test_spring_forward_fires_once_where_the_skipped_hour_would_have_been() -> None:
    """`0 2 * * *` has no 02:00 on the day the clocks go forward.

    The occurrence is not dropped for the day and not doubled: it lands on the
    instant 02:00 would have been, which a person in New York reads as 03:00.
    """
    schedule = CronSchedule.parse("0 2 * * *", zone=_NEW_YORK)
    day_before = schedule.next_fire_time(datetime(2026, 3, 6, 12, tzinfo=timezone.utc))
    assert day_before == datetime(2026, 3, 7, 7, 0, tzinfo=timezone.utc)

    gap_day = schedule.next_fire_time(day_before)
    assert gap_day == datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)
    assert gap_day.astimezone(ZoneInfo(_NEW_YORK)).strftime("%Y-%m-%d %H:%M") == (
        f"{_SPRING_FORWARD} 03:00"
    )

    # And back to 02:00 local from the next day, on the new offset.
    assert schedule.next_fire_time(gap_day) == datetime(
        2026, 3, 9, 6, 0, tzinfo=timezone.utc
    )


def test_fall_back_fires_once_on_the_first_of_the_two_local_times() -> None:
    """`0 1 * * *` has two 01:00s on the day the clocks go back.

    A daily schedule fires once a day, so it takes the earlier, pre-transition
    one and skips the repeat; 06:00Z is the second 01:00 and must not appear.
    """
    schedule = CronSchedule.parse("0 1 * * *", zone=_NEW_YORK)
    fires = []
    cursor = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    for _ in range(4):
        cursor = schedule.next_fire_time(cursor)
        assert cursor is not None
        fires.append(cursor)

    assert fires == [
        datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc),
    ]
    assert fires[1].astimezone(ZoneInfo(_NEW_YORK)).strftime("%Y-%m-%d %H:%M") == (
        f"{_FALL_BACK} 01:00"
    )


def test_a_fire_is_strictly_later_even_asked_from_inside_the_repeated_hour() -> None:
    """Monotonicity is what stops the poller re-firing the same occurrence.

    `claim_due_schedules` advances a schedule's cursor to whatever
    `next_fire_time` returns, so a value at or before the moment asked about
    would spin. Inside the repeated hour two instants share one wall clock and
    the `fold=0` rule maps both back to the earlier one -- reachable whenever a
    schedule is armed from a real "now" during that hour.
    """
    schedule = CronSchedule.parse("*/30 * * * *", zone=_NEW_YORK)
    second_pass = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)  # 01:00 EST
    assert schedule.next_fire_time(second_pass) == datetime(
        2026, 11, 1, 7, 0, tzinfo=timezone.utc
    )


def test_every_fire_across_both_transitions_moves_forward() -> None:
    """Stated as the invariant rather than as instants, over both transitions."""
    schedule = CronSchedule.parse("*/30 * * * *", zone=_NEW_YORK)
    for start in (
        datetime(2026, 3, 8, 4, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 4, tzinfo=timezone.utc),
    ):
        cursor = start
        for _ in range(12):
            nxt = schedule.next_fire_time(cursor)
            assert nxt is not None
            assert nxt > cursor, f"{nxt.isoformat()} is not after {cursor.isoformat()}"
            cursor = nxt


@pytest.mark.parametrize(
    "zone", ["america/new_york", "AMERICA/NEW_YORK", "Not/AZone", "EST5EDT/nope"]
)
def test_an_unresolvable_zone_is_refused(zone: str) -> None:
    """The miscased ones are the trap.

    `ZoneInfo` resolves its key as a path under TZPATH, so on macOS's
    case-insensitive filesystem `ZoneInfo("america/new_york")` succeeds and the
    same string raises on Linux. Validating by construction would pass here and
    fail in the container, so validation is membership of
    `available_timezones()` instead.
    """
    with pytest.raises(ValueError):
        CronSchedule.parse("0 9 * * *", zone=zone)


def test_no_zone_is_utc_and_so_is_naming_utc() -> None:
    """Absence must keep meaning exactly what it meant before zones existed.

    A stored "UTC" and a missing key have to be indistinguishable, or every
    schedule would need rewriting and every exported pod bundle would gain a
    diff to no effect.
    """
    start = datetime(2026, 3, 8, 4, tzinfo=timezone.utc)
    absent = CronSchedule.parse("*/30 * * * *")
    explicit = CronSchedule.parse("*/30 * * * *", zone="UTC")
    empty = CronSchedule.parse("*/30 * * * *", zone="   ")

    cursor_absent = cursor_explicit = cursor_empty = start
    for _ in range(8):
        cursor_absent = absent.next_fire_time(cursor_absent)
        cursor_explicit = explicit.next_fire_time(cursor_explicit)
        cursor_empty = empty.next_fire_time(cursor_empty)
        assert cursor_absent == cursor_explicit == cursor_empty

    assert absent.zone_name is None
    assert empty.zone_name is None
    assert explicit.zone_name == "UTC"
