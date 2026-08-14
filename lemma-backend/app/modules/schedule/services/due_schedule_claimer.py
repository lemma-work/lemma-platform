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
        # Advance from the claimed instant, not from `now`. Advancing from `now`
        # would silently skip every occurrence a backlog had fallen behind on,
        # and a poller that quietly drops work is worse than one that runs late.
        upcoming = next_cursor_for(row.config, after=fire_at)
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
