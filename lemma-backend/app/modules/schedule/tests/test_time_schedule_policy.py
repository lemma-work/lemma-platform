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
