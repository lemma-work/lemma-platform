from datetime import datetime, timedelta, timezone

import pytest

from app.modules.schedule.domain.errors import (
    ScheduleTooFrequentError,
    ScheduleValidationError,
)
from app.modules.schedule.services.time_schedule_policy import (
    validate_cron_expression,
    validate_time_schedule_config,
)


@pytest.mark.parametrize(
    "cron",
    [
        "* * * * *",
        "*/5 * * * *",
        "0,10 * * * *",
        "0,50 * * * *",
    ],
)
def test_rejects_cron_below_minimum_interval(cron: str) -> None:
    with pytest.raises(
        ScheduleTooFrequentError,
        match="Time schedules cannot run more frequently than every 15 minutes",
    ) as exc_info:
        validate_cron_expression(cron)

    assert exc_info.value.code == "SCHEDULE_TOO_FREQUENT"
    assert exc_info.value.status_code == 422


@pytest.mark.parametrize(
    "cron",
    [
        "*/15 * * * *",
        "0,15,30,45 * * * *",
        "0 * * * *",
        "0,20,40 * * * *",
        "0 9 * * 1-5",
    ],
)
def test_accepts_cron_at_or_above_minimum_interval(cron: str) -> None:
    assert validate_cron_expression(cron) is not None


def test_configurable_minimum_changes_policy_and_message(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.schedule.services.time_schedule_policy.schedule_settings.schedule_minimum_interval_minutes",
        30,
    )
    with pytest.raises(
        ScheduleTooFrequentError,
        match="Time schedules cannot run more frequently than every 30 minutes",
    ):
        validate_cron_expression("*/15 * * * *")

    assert validate_cron_expression("*/30 * * * *") is not None


def test_minimum_interval_message_uses_singular_minute() -> None:
    assert ScheduleTooFrequentError(1).message == (
        "Time schedules cannot run more frequently than every 1 minute."
    )


def test_one_time_schedule_is_not_subject_to_frequency_limit() -> None:
    now = datetime.now(timezone.utc)
    run_date = now + timedelta(minutes=1)

    assert (
        validate_time_schedule_config({"scheduled_at": run_date.isoformat()}, now=now)
        == run_date
    )


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"cron": "0 * * * *", "scheduled_at": "2099-01-01T00:00:00+00:00"},
    ],
)
def test_time_schedule_requires_exactly_one_trigger(config: dict) -> None:
    with pytest.raises(ScheduleValidationError, match="exactly one"):
        validate_time_schedule_config(config)


def test_an_unknown_timezone_is_a_422_not_a_500() -> None:
    """Naming a zone that does not exist is the user's mistake, told plainly."""
    with pytest.raises(ScheduleValidationError, match="Unknown time zone") as exc_info:
        validate_time_schedule_config(
            {"cron": "0 9 * * *", "timezone": "Europe/Berlim"}
        )

    assert exc_info.value.code == "SCHEDULE_VALIDATION_ERROR"
    assert exc_info.value.status_code == 422
    assert "IANA" in exc_info.value.message


def test_a_miscased_timezone_is_refused_rather_than_accepted_on_macos() -> None:
    """`ZoneInfo("america/new_york")` succeeds on a case-insensitive filesystem.

    Validating by constructing a `ZoneInfo` would accept this on a developer's
    Mac and fail in the Linux container, so the check is membership of
    `available_timezones()`. This is the case that catches the difference.
    """
    with pytest.raises(ScheduleValidationError, match="Unknown time zone"):
        validate_time_schedule_config(
            {"cron": "0 9 * * *", "timezone": "america/new_york"}
        )


def test_the_frequency_floor_is_measured_in_the_schedules_own_zone() -> None:
    """The floor must police the instants the poller will actually produce.

    01:00 and 03:00 are two hours apart on the wall clock and one hour apart in
    real time on the day the clocks go forward. Measured in UTC this expression
    never comes within 90 minutes of itself; measured in New York it does.
    """
    assert (
        validate_cron_expression("0 1,3 * * *", minimum_interval_minutes=90) is not None
    )

    with pytest.raises(ScheduleTooFrequentError):
        validate_cron_expression(
            "0 1,3 * * *",
            zone="America/New_York",
            minimum_interval_minutes=90,
        )


def test_a_scheduled_at_without_an_offset_is_read_in_the_schedules_zone() -> None:
    """ "09:00 on the first" means where the author is, not in UTC."""
    parsed = validate_time_schedule_config(
        {"scheduled_at": "2099-07-01T09:00:00", "timezone": "Europe/Berlin"}
    )
    assert parsed == datetime(2099, 7, 1, 7, 0, tzinfo=timezone.utc)


def test_a_scheduled_at_with_an_offset_keeps_its_own_instant() -> None:
    """An explicit offset already names an instant; the zone must not move it."""
    parsed = validate_time_schedule_config(
        {"scheduled_at": "2099-07-01T09:00:00+00:00", "timezone": "Europe/Berlin"}
    )
    assert parsed == datetime(2099, 7, 1, 9, 0, tzinfo=timezone.utc)


def test_no_timezone_key_still_means_utc() -> None:
    """Absence has to keep behaving exactly as it did before zones existed."""
    without = validate_time_schedule_config({"scheduled_at": "2099-07-01T09:00:00"})
    explicit = validate_time_schedule_config(
        {"scheduled_at": "2099-07-01T09:00:00", "timezone": "UTC"}
    )
    assert without == explicit == datetime(2099, 7, 1, 9, 0, tzinfo=timezone.utc)
