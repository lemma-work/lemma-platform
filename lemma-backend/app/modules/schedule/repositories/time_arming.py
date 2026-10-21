"""Arming a TIME schedule is writing its next ``schedule.fire`` item.

Called wherever ``next_fire_at`` is written, in the same transaction, so a
schedule is never armed in the row and missing from the queue. Items for
superseded cursors are left in place: the fire handler only acts while the
cursor still equals the item's ``fire_at``, so they end as no-ops.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.work import WorkWriter
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.domain.time_cursor import next_cursor_for
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.work import registration as _registration  # noqa: F401  registers the kinds this writes
from app.modules.schedule.work.kinds import (
    SCHEDULE_ARM,
    SCHEDULE_FIRE,
    ScheduleRef,
    TimeFire,
    fire_dedup_key,
    schedule_namespace,
)


def cursor_for(
    config: Mapping[str, object] | None,
    *,
    is_active: bool,
    schedule_type: ScheduleType,
) -> datetime | None:
    """A schedule row's cursor, or ``None`` if it is not armed.

    ``None`` covers everything that must not fire: inactive rows, non-TIME
    schedules (driven by their own triggers), unusable expressions, and
    one-shots whose moment has passed.
    """
    if not is_active or schedule_type != ScheduleType.TIME:
        return None
    now = datetime.now(timezone.utc)
    cursor = next_cursor_for(config, after=now)
    if cursor is None or cursor <= now:
        return None
    return cursor


async def recompute_cursor(
    session: AsyncSession, schedule_id: UUID, update_data: dict[str, object]
) -> Schedule | None:
    """Put the new cursor in ``update_data`` when what it derives from changes.

    The row is locked before the cursor is computed: a fire holding it commits
    its own next cursor first, and the edit then computes from that, so the
    two can never leave two fire chains behind. Returns the locked row, or
    ``None`` when the edit does not touch the cursor.
    """
    if "config" not in update_data and "is_active" not in update_data:
        return None
    row = await session.get(
        Schedule, schedule_id, with_for_update=True, populate_existing=True
    )
    if row is not None:
        config = update_data.get("config", row.config)
        active = update_data.get("is_active", row.is_active)
        update_data["next_fire_at"] = cursor_for(
            config if isinstance(config, Mapping) else None,
            is_active=bool(active),
            schedule_type=row.schedule_type,
        )
    return row


async def arm_time_fire(
    session: AsyncSession,
    *,
    schedule_id: UUID,
    pod_id: UUID | None,
    fire_at: datetime,
    work: WorkWriter | None = None,
) -> None:
    await (work or WorkWriter(session)).add(
        SCHEDULE_FIRE,
        schedule_namespace(pod_id),
        TimeFire(schedule_id=schedule_id, fire_at=fire_at),
        dedup_key=fire_dedup_key(schedule_id, fire_at),
        run_at=fire_at,
    )


async def request_arm(
    session: AsyncSession, *, schedule_id: UUID, pod_id: UUID | None
) -> None:
    """Ask for a cursor to be computed and armed (rows written outside the paths above)."""
    await WorkWriter(session).add(
        SCHEDULE_ARM,
        schedule_namespace(pod_id),
        ScheduleRef(schedule_id=schedule_id),
        dedup_key=f"schedule-arm:{schedule_id}",
    )
