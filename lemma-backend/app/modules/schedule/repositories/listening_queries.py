"""Which schedules hold which account subscriptions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule


async def schedules_listening_through(
    session: AsyncSession, provider_trigger_ids: list[str]
) -> dict[str, ScheduleEntity]:
    """The webhook schedules that hold these subscription ids, active or not,
    by id -- for telling a live subscription from an orphan."""
    if not provider_trigger_ids:
        return {}
    key = Schedule.config["provider_trigger_id"].astext
    rows = (
        await session.execute(
            select(Schedule).where(
                Schedule.schedule_type == ScheduleType.WEBHOOK,
                key.in_(provider_trigger_ids),
            )
        )
    ).scalars()
    found: dict[str, ScheduleEntity] = {}
    for row in rows:
        entity = row.to_entity()
        found[str(entity.config.get("provider_trigger_id"))] = entity
    return found
