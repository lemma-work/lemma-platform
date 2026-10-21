"""A schedule's last fire, read from its newest run instead of stamped on the row.

Stamping ``last_fired_at`` & co. on ``schedules`` was an UPDATE of a shared
row on every event a trigger saw, matched or not. The ledger already has the
answer: the newest ``schedule_runs`` row, one index probe per schedule shown --
or the newest ``trigger_skips`` row when that is later: a change the trigger let
pass (its condition did not hold, a bulk write it skips) starts no run, and
"skipped" must still read differently from "never arrived".
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleRunStatus,
)

_LATEST = text(
    """
    SELECT s.id AS schedule_id, r.created_at, r.target_run_id, r.status,
           r.target_outcome, r.error_type
      FROM unnest(CAST(:ids AS uuid[])) AS s(id)
      CROSS JOIN LATERAL (
            SELECT created_at, target_run_id, status, target_outcome, error_type
              FROM schedule_runs
             WHERE schedule_id = s.id
             ORDER BY created_at DESC, id DESC
             LIMIT 1
      ) r
    """
)

_LATEST_SKIP = text(
    """
    SELECT s.id AS schedule_id, k.at
      FROM unnest(CAST(:ids AS uuid[])) AS s(id)
      CROSS JOIN LATERAL (
            SELECT at FROM trigger_skips
             WHERE schedule_id = s.id
             ORDER BY at DESC
             LIMIT 1
      ) k
    """
)

_ERRORS = frozenset(
    {
        ScheduleRunStatus.FAILED.value,
        ScheduleRunStatus.DEAD_LETTERED.value,
        ScheduleRunStatus.TARGET_FAILED.value,
    }
)
_SKIPS = frozenset(
    {ScheduleRunStatus.FILTERED.value, ScheduleRunStatus.FILTERING.value}
)


def fire_status_of(status: str) -> ScheduleFireStatus:
    if status in _SKIPS:
        return ScheduleFireStatus.FILTERED
    if status in _ERRORS:
        return ScheduleFireStatus.ERROR
    return ScheduleFireStatus.TRIGGERED


async def with_last_fire(
    session: AsyncSession, entities: Sequence[ScheduleEntity]
) -> None:
    """Fill each entity's ``last_*`` fields from its newest run, in place."""
    if not entities:
        return
    ids: list[UUID] = [entity.id for entity in entities]
    rows = (await session.execute(_LATEST, {"ids": ids})).all()
    latest = {row.schedule_id: row for row in rows}
    skips = await session.execute(_LATEST_SKIP, {"ids": ids})
    skipped = {row.schedule_id: row.at for row in skips.all()}
    for entity in entities:
        row = latest.get(entity.id)
        skipped_at = skipped.get(entity.id)
        # A page that skips one change and fires another writes both in one
        # transaction; the skip's ``at`` is that transaction's start, so the
        # run, created inside it, wins the tie.
        if skipped_at is not None and (row is None or skipped_at > row.created_at):
            entity.last_fired_at = skipped_at
            entity.last_run_id = None
            entity.last_fire_status = ScheduleFireStatus.FILTERED
            entity.last_error = None
            continue
        if row is None:
            continue
        status = row.target_outcome or row.status
        entity.last_fired_at = row.created_at
        entity.last_run_id = row.target_run_id
        entity.last_fire_status = fire_status_of(status)
        entity.last_error = row.error_type if status in _ERRORS else None
