"""Deleting every schedule attached to something that is going away.

Three callers, one shape: a pod being deleted, and the two things a schedule can
actually fire — a workflow and an agent. Each hands over a list of schedules and
wants them gone with their external state torn down.

A module of its own rather than three more methods on `ScheduleService`. That
file is already past the size the architecture ratchet allows, so the family
moved out together instead of growing it; the service keeps `delete_all_for_pod`
as its published name and delegates here.

The pod case and the target cases differ in *when* they run, and that difference
is deliberate. Pod deletion disarms inline and tears down on the pod-deleted
event, because a pod holds an unbounded number of webhook schedules and each
teardown is a Composio round trip — not something to put inside the deleting
request's transaction. A workflow or an agent holds the handful of schedules its
author pointed at it, and `PS-SCHED-030` asks for the dangling target to be
prevented rather than reported, so those run inline and the schedule is gone by
the time the delete returns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.domain.errors import ScheduleInfrastructureError

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from app.modules.schedule.services.schedule_service import ScheduleService

logger = get_logger(__name__)


async def delete_all_for_pod(service: "ScheduleService", pod_id: UUID) -> int:
    """Every schedule in a pod. System-level: no RBAC, includes internal rows."""
    return await _delete_each(
        service, await service.schedule_repository.list_all_by_pod(pod_id)
    )


async def delete_all_for_workflow(service: "ScheduleService", workflow_id: UUID) -> int:
    """Every schedule pointing at a workflow, for workflow deletion."""
    return await _delete_each(
        service, await service.schedule_repository.list_all_by_workflow(workflow_id)
    )


async def delete_all_for_agent(service: "ScheduleService", agent_id: UUID) -> int:
    """Every schedule pointing at an agent, for agent deletion."""
    return await _delete_each(
        service, await service.schedule_repository.list_all_by_agent(agent_id)
    )


async def _delete_each(
    service: "ScheduleService", schedules: List[ScheduleEntity]
) -> int:
    """Delete each schedule, keeping going when its external teardown fails.

    Best-effort in one specific respect: an *external* teardown failure
    (APScheduler/Composio) does not abort the rest, and the row is force-deleted
    anyway so the schedule can no longer fire. A database failure is not
    best-effort and propagates -- swallowing it left the session
    rollback-pending and the caller's commit failing later with an error naming
    none of this, under a pod reported cleaned up.
    """
    deleted = 0
    for schedule in schedules:
        try:
            if await service.delete_schedule(schedule.id):
                deleted += 1
        except ScheduleInfrastructureError:
            # ``delete_schedule`` wraps every external failure in this, so this
            # arm is the external teardown and nothing else.
            logger.debug(
                "schedule.cleanup.primary_failed",
                schedule_id=schedule.id,
                pod_id=schedule.pod_id,
                exc_info=True,
            )
            if await service.schedule_repository.delete(schedule.id):
                deleted += 1
    return deleted
