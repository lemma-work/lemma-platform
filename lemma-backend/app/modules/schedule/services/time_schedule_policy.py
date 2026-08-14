"""Canonical validation for recurring and one-time TIME schedules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.cron import CronSchedule
from app.modules.schedule.domain.errors import (
    ScheduleTooFrequentError,
    ScheduleValidationError,
)


_VALIDATION_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_VALIDATION_END = datetime(2052, 1, 1, tzinfo=timezone.utc)
_MAX_VALIDATION_OCCURRENCES = 2_048


def validate_cron_expression(
    cron_expression: str,
    *,
    minimum_interval_minutes: int | None = None,
) -> CronSchedule:
    """Parse a five-field UTC cron and enforce the configured frequency floor."""
    try:
        schedule = CronSchedule.parse(cron_expression)
    except (TypeError, ValueError) as exc:
        raise ScheduleValidationError(str(exc)) from exc

    minimum_minutes = (
        minimum_interval_minutes
        if minimum_interval_minutes is not None
        else schedule_settings.schedule_minimum_interval_minutes
    )
    minimum_interval = timedelta(minutes=minimum_minutes)
    previous: datetime | None = None

    for next_fire in schedule.fire_times_from(
        _VALIDATION_START, limit=_MAX_VALIDATION_OCCURRENCES
    ):
        if next_fire >= _VALIDATION_END:
            break
        if previous is not None and next_fire - previous < minimum_interval:
            raise ScheduleTooFrequentError(minimum_minutes)
        previous = next_fire

    if previous is None:
        raise ScheduleValidationError(
            "Cron expression does not produce a valid execution time."
        )
    return schedule


def validate_time_schedule_config(
    config: dict[str, Any],
    *,
    now: datetime | None = None,
) -> datetime | CronSchedule:
    """Validate one and only one TIME trigger, returning its parsed value."""
    cron = config.get("cron")
    scheduled_at = config.get("scheduled_at")
    if bool(cron) == bool(scheduled_at):
        raise ScheduleValidationError(
            "TIME schedules must declare exactly one of cron or scheduled_at."
        )

    if cron:
        return validate_cron_expression(str(cron))

    try:
        run_date = datetime.fromisoformat(str(scheduled_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleValidationError(
            f"Invalid scheduled_at timestamp: {scheduled_at}"
        ) from exc
    if run_date.tzinfo is None:
        run_date = run_date.replace(tzinfo=timezone.utc)
    else:
        run_date = run_date.astimezone(timezone.utc)
    if run_date <= (now or datetime.now(timezone.utc)):
        raise ScheduleValidationError("scheduled_at must be in the future.")
    return run_date
