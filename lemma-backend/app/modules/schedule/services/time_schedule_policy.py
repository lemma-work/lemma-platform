"""Canonical validation for recurring and one-time TIME schedules."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.concurrency.offload import run_blocking
from app.core.infrastructure.db.transaction_locks import connection_released
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
    session: object | None = None,
) -> datetime | CronSchedule:
    """:func:`validate_time_schedule_config`, off the event loop.

    Policing the frequency floor means walking fire times, and a dense
    expression walks thousands of them through a pure-Python cron library. Run
    inline from a request handler that is what a second-long loop stall looks
    like, so callers on the loop use this.

    ``session`` is handed back for the duration when given. Every caller is a
    schedule write in the middle of a request's unit of work, so the pooled
    connection was otherwise held across a thread hop *and* the wait for a slot
    on the ``cpu_bound`` limiter -- which under contention is the longer of the
    two. Nothing in here reads a database, so there is nothing to re-acquire
    for; ``None`` is a no-op.
    """
    async with connection_released(session):
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


_TIME_FILTER_REFUSAL = (
    "A time schedule fires on the clock, so there is no event for a filter to "
    "judge. Leave filter_instruction and filter_output_schema empty, or use a "
    "webhook or table-change trigger."
)


def refuse_time_schedule_filter(
    filter_instruction: str | None,
    filter_output_schema: Mapping[str, object] | None,
) -> None:
    """A new TIME schedule may not carry a filter: nothing would ever ask it.

    It was accepted before and silently never evaluated, which is worse than
    being told.
    """
    if (filter_instruction or "").strip() or filter_output_schema:
        raise ScheduleValidationError(_TIME_FILTER_REFUSAL)


def refuse_new_time_schedule_filter(
    stored_instruction: str | None,
    stored_schema: Mapping[str, object] | None,
    update_data: Mapping[str, object],
) -> None:
    """An update may clear a TIME schedule's filter or resend the one it has.

    Schedules saved before the filter was refused here still carry one, and a
    client that edits the cron and sends the whole form back sends it too.
    Refusing that would make those schedules uneditable; only a filter that is
    new is refused.
    """
    instruction = update_data.get("filter_instruction", stored_instruction)
    schema = update_data.get("filter_output_schema", stored_schema)
    cleared = not (isinstance(instruction, str) and instruction.strip())
    unchanged = instruction == stored_instruction and schema == stored_schema
    # A schema with no instruction is never asked, so clearing the instruction
    # alone is enough to clear the filter.
    if cleared or unchanged:
        return
    raise ScheduleValidationError(_TIME_FILTER_REFUSAL)
