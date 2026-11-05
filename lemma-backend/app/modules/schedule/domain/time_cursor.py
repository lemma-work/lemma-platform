"""When a TIME schedule fires next. Pure: no clock of its own, no I/O.

Every path that arms a TIME schedule runs through ``next_cursor_for`` -- the
write paths, the fire handler, the arm handler -- so the config's
``timezone`` is read here and nowhere else. Absent means UTC, which is what
schedules written before zones existed already meant.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from app.modules.schedule.domain.cron import CronSchedule, resolve_zone

#: Why a schedule was retired for having no future fire, in the vocabulary the
#: paused-schedule email already speaks.
NO_FUTURE_FIRE = "SCHEDULE_VALIDATION_ERROR"
ONE_TIME_MISSED = "SCHEDULE_ONE_TIME_MISSED"

# How far a single fire may walk forward to escape a backlog. A minute-cron
# down for a day is 1440 occurrences; the cap keeps one fire's catch-up bounded
# and lands the rest on the next fire.
_MAX_CATCHUP_STEPS = 2048


def is_one_shot(config: Mapping[str, object] | None) -> bool:
    return not (config or {}).get("cron")


def next_cursor_for(
    config: Mapping[str, object] | None, *, after: datetime
) -> datetime | None:
    """When a schedule should next fire after ``after``, or ``None`` if never.

    Cron rows recompute from the expression. One-shot rows have exactly one
    occurrence, so once it is fired there is no next -- returning ``None`` is
    what retires them.
    """
    settings = config or {}
    zone_value = settings.get("timezone")
    zone_name = None if zone_value is None else str(zone_value)
    cron = settings.get("cron")
    if cron:
        try:
            return CronSchedule.parse(str(cron), zone=zone_name).next_fire_time(after)
        except ValueError:
            # An unparseable expression -- or a zone this host cannot resolve --
            # cannot be scheduled; the arm handler retires it, saying why.
            return None
    scheduled_at = settings.get("scheduled_at")
    if not scheduled_at:
        return None
    try:
        moment = datetime.fromisoformat(str(scheduled_at))
        zone = resolve_zone(zone_name)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return moment.astimezone(timezone.utc)


def coalesced_cursor(
    config: Mapping[str, object] | None, *, fire_at: datetime, now: datetime
) -> datetime | None:
    """Where the cursor lands after firing the occurrence due at ``fire_at``.

    One step forward is right when a schedule is a tick late. After an outage
    it is wrong: a minute-cron down for three hours has 180 due occurrences,
    and replaying them is 180 emails, 180 agent runs. The backlog collapses
    into the one fire being made, and the cursor resumes at the next
    occurrence that is genuinely in the future. The schedule still fires,
    once, late -- it is not dropped.
    """
    cursor = next_cursor_for(config, after=fire_at)
    for _ in range(_MAX_CATCHUP_STEPS):
        if cursor is None or cursor > now:
            return cursor
        cursor = next_cursor_for(config, after=cursor)
    return cursor


def initial_cursor(
    config: Mapping[str, object] | None, *, now: datetime
) -> tuple[datetime | None, str | None]:
    """A cursor for an active schedule that has none: ``(cursor, retire_reason)``.

    A one-shot whose moment has passed is retired, not fired late -- "send this
    at 09:00" is rarely still wanted at 14:00. A config that can never fire is
    retired too, rather than left active and invisible.
    """
    upcoming = next_cursor_for(config, after=now)
    if upcoming is None:
        return None, NO_FUTURE_FIRE
    if is_one_shot(config) and upcoming <= now:
        return None, ONE_TIME_MISSED
    return upcoming, None
