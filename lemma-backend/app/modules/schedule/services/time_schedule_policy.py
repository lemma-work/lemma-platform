"""Canonical validation for recurring and one-time TIME schedules."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.concurrency.offload import run_blocking
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.cron import CronSchedule, resolve_zone
from app.modules.schedule.domain.errors import (
    ScheduleTooFrequentError,
    ScheduleValidationError,
)


_VALIDATION_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
_VALIDATION_END = datetime(2052, 1, 1, tzinfo=timezone.utc)
_MAX_VALIDATION_OCCURRENCES = 2_048

#: A TIME schedule's trigger config as it arrives from the API: an open
#: mapping, because the trigger shape is validated here rather than by type.
TimeScheduleConfig = dict[str, Any]


def validate_cron_expression(
    cron_expression: str,
    *,
    zone: str | None = None,
    minimum_interval_minutes: int | None = None,
) -> CronSchedule:
    """Parse a five-field cron in ``zone`` and enforce the frequency floor.

    The floor is walked in the schedule's own zone because that is what the
    poller will produce. Two wall-clock times can be closer together in real
    elapsed time than they look -- across a spring-forward transition 01:30 and
    03:00 are half an hour apart, not ninety minutes -- so measuring the
    expression in UTC would police instants that never happen.
    """
    try:
        schedule = CronSchedule.parse(cron_expression, zone=zone)
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


def zone_name_of(config: Mapping[str, object]) -> str | None:
    """The IANA zone a TIME config's wall-clock times are read in.

    Absent means UTC, and stays absent: writing ``"UTC"`` into every config that
    lacks the key would give every existing schedule a diff to no effect.
    """
    zone = config.get("timezone")
    return None if zone is None else str(zone)


async def validated_time_schedule_config(
    config: TimeScheduleConfig,
    *,
    now: datetime | None = None,
) -> datetime | CronSchedule:
    """:func:`validate_time_schedule_config`, off the event loop.

    Policing the frequency floor means walking fire times, and a dense
    expression walks thousands of them through a pure-Python cron library. Run
    inline from a request handler that is what a second-long loop stall looks
    like, so callers on the loop use this.
    """
    return await run_blocking(
        validate_time_schedule_config, config, now=now, limiter="cpu_bound"
    )


def validate_time_schedule_config(
    config: TimeScheduleConfig,
    *,
    now: datetime | None = None,
) -> datetime | CronSchedule:
    """Validate one and only one TIME trigger, returning its parsed value."""
    cron = config.get("cron")
    scheduled_at = config.get("scheduled_at")
    zone_name = zone_name_of(config)
    if bool(cron) == bool(scheduled_at):
        raise ScheduleValidationError(
            "TIME schedules must declare exactly one of cron or scheduled_at."
        )

    if cron:
        return validate_cron_expression(str(cron), zone=zone_name)

    try:
        zone = resolve_zone(zone_name)
    except ValueError as exc:
        raise ScheduleValidationError(str(exc)) from exc
    try:
        run_date = datetime.fromisoformat(str(scheduled_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleValidationError(
            f"Invalid scheduled_at timestamp: {scheduled_at}"
        ) from exc
    # An explicit offset in the timestamp wins: it already names an instant, and
    # a `timezone` that disagreed with it would have to override the more
    # specific of the two. A bare wall-clock time is read in the schedule's zone
    # rather than in UTC, so "09:00 on the first" means what it says.
    if run_date.tzinfo is None:
        run_date = run_date.replace(tzinfo=zone)
    run_date = run_date.astimezone(timezone.utc)
    if run_date <= (now or datetime.now(timezone.utc)):
        raise ScheduleValidationError("scheduled_at must be in the future.")
    return run_date
