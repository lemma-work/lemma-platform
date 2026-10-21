"""A deleted pod's schedules go with it, as a work item written by the deletion.

The delete transaction already disarms every schedule inline, so nothing fires
in the meantime; this is the rest -- deleting the rows and tearing down the
provider triggers behind them, which is network I/O and so a JOB. It runs under
the organization's namespace because the deletion drops the pod's own queue.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.pod.contracts.lifecycle import PodDeleted

logger = get_logger(__name__)

SCHEDULE_POD_DELETED = "schedule.pod_deleted"


class SchedulePodDeletedPayload(BaseModel):
    pod_id: UUID


async def run_schedule_pod_deleted(
    ctx: JobContext, payload: SchedulePodDeletedPayload
) -> None:
    from app.modules.schedule.api.dependencies import get_schedule_service

    # A checkpoint, not the fenced completion: the teardown runs after its
    # commit, and a completion transaction must not wait on a provider. A
    # repeat finds the rows already gone and deletes nothing.
    async with ctx.checkpoint() as uow:
        deleted = await get_schedule_service(uow).delete_all_for_pod(payload.pod_id)
    if deleted:
        logger.info(
            "schedule.pod_deleted.schedules_deleted",
            pod_id=str(payload.pod_id),
            count=deleted,
        )


def register_schedule_pod_deleted_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SCHEDULE_POD_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=SchedulePodDeletedPayload,
    )(run_schedule_pod_deleted)


register_schedule_pod_deleted_work(default_registry)
facts.subscribe(
    PodDeleted,
    kind=SCHEDULE_POD_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"schedule-pod-deleted:{fact.pod_id}",
)
