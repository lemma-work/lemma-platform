"""Claiming due schedules, so N replicas fire each occurrence exactly once.

This replaces what APScheduler's job store was actually providing. It was never
the source of truth -- ``reconcile_time_schedule_jobs`` rebuilt the whole logical
job set from ``schedules`` on every boot -- it was a due-time index plus a claim.

The claim is the part that matters for running more than one replica, and it is
one SQL statement:

    SELECT ... WHERE next_fire_at <= now()
      ORDER BY next_fire_at
      FOR UPDATE SKIP LOCKED
      LIMIT n

``FOR UPDATE`` takes a row lock; ``SKIP LOCKED`` means a replica that loses the
race walks past the row instead of blocking behind it. Advancing ``next_fire_at``
in the *same transaction* is what makes the claim stick: until that transaction
commits nobody else sees the new cursor, and once it does the occurrence is gone.

Deliberately not leader election. A leader is a single point of failure and
leaves a gap between "leader died" and "lease expired" where nothing is
scheduled at all. Claiming has no leader, every replica does useful work, and it
degrades to one healthy replica rather than to none. ``outbox.py`` already uses
this shape here, so it is a proven pattern in this codebase rather than a new
one.

The ledger behind this stays as the second line of defence: ``ScheduleRun``
carries a unique constraint on ``(schedule_id, source_event_id)``, and the key is
derived from the *claimed* fire time, so even a double-fire from clock skew or a
retry collapses into one run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.core.log.log import get_logger
from app.modules.schedule.domain.cron import CronSchedule
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule

logger = get_logger(__name__)

DEFAULT_CLAIM_LIMIT = 100


@dataclass(frozen=True, slots=True)
class ClaimedFire:
    """One occurrence this replica owns, and nobody else will fire."""

    schedule_id: UUID
    user_id: UUID | None
    fire_at: datetime
    is_one_shot: bool
    # Read from the schedule row, not carried by a job. The job store never held
    # this either -- `SchedulerAPIClient` copied it out of `config["payload"]`
    # on the way in, which is one more way the store held nothing authoritative.
    payload: dict


# How far a single claim may walk forward to escape a backlog. A minute-cron
# down for a day is 1440 occurrences; the cap keeps one row's catch-up bounded
# and hands the rest to the next tick rather than holding the claim
# transaction -- and its `FOR UPDATE` locks -- while it walks.
_MAX_CATCHUP_STEPS = 2048


def _coalesced_cursor(
    config: dict, *, fire_at: datetime, now: datetime
) -> datetime | None:
    """Where the cursor lands after firing the occurrence claimed at ``fire_at``.

    One step forward is right when a schedule is a tick late. It is wrong after
    an outage: a minute-cron down for three hours has 180 due occurrences, and
    stepping one per claim replays all 180 as separate late fires -- 180 emails,
    180 agent runs -- trickled out over as many polls.

    APScheduler did not do that. Nothing here ever set ``coalesce`` or
    ``misfire_grace_time``, so its defaults applied: ``coalesce=True`` collapsed
    a backlog into a single run, and ``misfire_grace_time=1`` dropped that run if
    it was more than a second late. Replaying the backlog is therefore not
    parity with the old behaviour -- it is strictly more firing than the system
    has ever done.

    So the backlog collapses into the one fire already claimed, and the cursor
    resumes at the next occurrence that is genuinely in the future. Work is not
    dropped -- the schedule does fire, once, and late, which is the part
    APScheduler got wrong by dropping it entirely.
    """
    cursor = next_cursor_for(config, after=fire_at)
    for _ in range(_MAX_CATCHUP_STEPS):
        if cursor is None or cursor > now:
            return cursor
        cursor = next_cursor_for(config, after=cursor)
    return cursor


def next_cursor_for(config: dict, *, after: datetime) -> datetime | None:
    """When a schedule should next fire, or ``None`` if never again.

    Cron rows recompute from the expression. One-shot rows have exactly one
    occurrence, so once it is claimed there is no next -- returning ``None`` is
    what retires them.
    """
    cron = (config or {}).get("cron")
    if cron:
        try:
            return CronSchedule.parse(cron).next_fire_time(after)
        except ValueError:
            # An unparseable expression cannot be scheduled. Reconciliation
            # deactivates these; refusing to guess a cursor keeps the row out of
            # the due index until it does.
            return None
    scheduled_at = (config or {}).get("scheduled_at")
    if not scheduled_at:
        return None
    try:
        moment = datetime.fromisoformat(str(scheduled_at))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


async def claim_due_schedules(
    session,
    *,
    now: datetime,
    limit: int = DEFAULT_CLAIM_LIMIT,
) -> list[ClaimedFire]:
    """Take ownership of every schedule due at ``now``, up to ``limit``.

    The caller must commit: the claim is only real once the transaction that
    advanced the cursors commits, and holding it open across the dispatch that
    follows would pin a connection for the length of a fan-out.

    Returns the *claimed* fire times, not ``now``. The dedup key is built from
    them, so a schedule that ran late still produces the key its occurrence
    would have had -- which is what lets the ledger recognise a duplicate.
    """
    statement = (
        select(Schedule)
        .where(
            Schedule.schedule_type == ScheduleType.TIME,
            Schedule.is_active.is_(True),
            Schedule.next_fire_at.is_not(None),
            Schedule.next_fire_at <= now,
        )
        .order_by(Schedule.next_fire_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.scalars(statement)).all())

    claimed: list[ClaimedFire] = []
    for row in rows:
        fire_at = row.next_fire_at
        if fire_at is None:  # pragma: no cover - guarded by the WHERE clause
            continue
        # Advance from the claimed instant, not from `now`: the occurrence that
        # was due is fired, late, rather than skipped. But advance *past* the
        # backlog rather than one step into it -- see `_coalesced_cursor`.
        upcoming = _coalesced_cursor(row.config, fire_at=fire_at, now=now)
        is_one_shot = not (row.config or {}).get("cron")
        if is_one_shot or upcoming is None:
            row.next_fire_at = None
            row.is_active = False
        else:
            row.next_fire_at = upcoming
        claimed.append(
            ClaimedFire(
                schedule_id=row.id,
                user_id=row.user_id,
                fire_at=fire_at.astimezone(timezone.utc),
                is_one_shot=is_one_shot,
                payload=dict((row.config or {}).get("payload") or {}),
            )
        )

    if claimed:
        logger.debug(
            "schedule.due_claimer.claimed.observed",
            claimed_count=len(claimed),
        )
    return claimed


async def backfill_missing_cursors(
    session,
    *,
    now: datetime,
    limit: int = DEFAULT_CLAIM_LIMIT,
) -> int:
    """Give active TIME schedules a ``next_fire_at`` if they have none.

    Rows arrive without a cursor two ways: they predate the column, or they were
    just written by the API and nothing has computed their first fire yet.
    Backfilling here rather than in a startup pass means there is no ordering
    requirement between deploy and first poll, and no separate code path to
    forget -- a row is either due, or scheduled, or unusable, and one tick moves
    it to whichever it is.

    Claimed the same way as a due fire, so replicas backfilling concurrently do
    not fight: whoever takes the row lock computes the cursor.

    A row whose config cannot produce a fire time is deactivated rather than
    left cursor-less, which would leave it invisible forever while still
    reading as active to anyone looking at the table.
    """
    statement = (
        select(Schedule)
        .where(
            Schedule.schedule_type == ScheduleType.TIME,
            Schedule.is_active.is_(True),
            Schedule.next_fire_at.is_(None),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list((await session.scalars(statement)).all())

    scheduled = 0
    retired = 0
    for row in rows:
        upcoming = next_cursor_for(row.config, after=now)
        is_one_shot = not (row.config or {}).get("cron")
        # A one-shot whose moment has already passed is dropped, not scheduled.
        # Backfilling it with a past cursor would make it immediately due and
        # fire it late -- "send this at 09:00" is rarely still wanted at 14:00,
        # and the reconcile pass this replaces deactivated those rows too
        # (SCHEDULE_ONE_TIME_MISSED). A cron whose next fire is somehow in the
        # past is a different case and stays claimable: it is *recurring*, so
        # running it late is the correct behaviour for a backlog.
        missed_one_shot = is_one_shot and upcoming is not None and upcoming <= now
        if upcoming is None or missed_one_shot:
            row.is_active = False
            retired += 1
            continue
        row.next_fire_at = upcoming
        scheduled += 1

    if scheduled or retired:
        logger.info(
            "schedule.due_claimer.cursors_backfilled",
            scheduled_count=scheduled,
            retired_count=retired,
        )
    return scheduled
