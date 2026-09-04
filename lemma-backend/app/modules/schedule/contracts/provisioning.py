"""What another module does to a pod's schedules when it builds one.

Three operations, not `ScheduleService`. `get_schedule_by_name` is the one that
had no name before: the service has no get-by-name at all, so every caller
asking "does this pod already have this schedule" wrote the same filtered list
and read the first page's first row. That is a schedule-shaped question, so it
is answered here.

The sibling of `dispatch.py`, and a submodule for the same reason: this reaches
the service layer, and `contracts/__init__` is imported by anything that wants
any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.modules.schedule.api.dependencies import get_schedule_service
from app.modules.schedule.domain.schedule import ScheduleCreateEntity, ScheduleEntity


async def list_schedules(uow, *, pod_id: UUID, ctx: Context) -> list[ScheduleEntity]:
    """Every schedule in the pod this reader may see."""
    schedules, _ = await get_schedule_service(uow).list_schedules(
        pod_id=pod_id, limit=1000, ctx=ctx
    )
    return list(schedules)


async def get_schedule_by_name(
    uow, *, pod_id: UUID, name: str, ctx: Context
) -> ScheduleEntity | None:
    """The named schedule, or ``None`` when the pod does not have one."""
    schedules, _ = await get_schedule_service(uow).list_schedules(
        pod_id=pod_id, name=name, ctx=ctx
    )
    return schedules[0] if schedules else None


async def create_schedule(
    uow, schedule: ScheduleCreateEntity, *, ctx: Context
) -> ScheduleEntity:
    """Create a schedule, along with whatever provider registration it needs."""
    return await get_schedule_service(uow).create_schedule(schedule, ctx)


__all__ = ["create_schedule", "get_schedule_by_name", "list_schedules"]
