"""What surfaces do when a pod is deleted or loses a member, as work items.

Both are written by the pod transaction that made them true, through the pod
lifecycle facts, so neither can be lost between a commit and a consumer.

* Pod deleted: every surface in the pod is removed so its accounts become free.
  A JOB, because removing a surface may deregister it with its provider. Under
  the organization's namespace: the deletion drops the pod's own queue.
* Member removed: the pod's administrators are told if the member looked after
  any contacts. A JOB too -- the notification it writes may be pushed.
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
from app.modules.pod.contracts.lifecycle import PodDeleted, PodMemberRemoved

SURFACES_POD_DELETED = "surfaces.pod_deleted"
SURFACES_MEMBER_REMOVED = "surfaces.member_removed"


class SurfacesPodDeletedPayload(BaseModel):
    pod_id: UUID


class SurfacesMemberRemovedPayload(BaseModel):
    pod_id: UUID
    user_id: UUID


async def delete_deleted_pod_surfaces(
    ctx: JobContext, payload: SurfacesPodDeletedPayload
) -> None:
    from app.modules.agent_surfaces.composition import build_surface_service

    # A checkpoint, so provider deregistration runs after its commit rather
    # than inside the fenced completion. A repeat finds nothing left to remove.
    async with ctx.checkpoint() as uow:
        await build_surface_service(uow).delete_all_surfaces_for_pod(payload.pod_id)


async def tell_admins_member_left(
    ctx: JobContext, payload: SurfacesMemberRemovedPayload
) -> None:
    from app.modules.agent_surfaces.services.contact_keepers import (
        tell_admins_a_keeper_left,
    )

    async with ctx.complete_work() as uow:
        await tell_admins_a_keeper_left(
            uow, pod_id=payload.pod_id, user_id=payload.user_id
        )


def register_surface_pod_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACES_POD_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=SurfacesPodDeletedPayload,
    )(delete_deleted_pod_surfaces)
    registry.handler(
        kind=SURFACES_MEMBER_REMOVED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=SurfacesMemberRemovedPayload,
    )(tell_admins_member_left)


register_surface_pod_work(default_registry)
facts.subscribe(
    PodDeleted,
    kind=SURFACES_POD_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"surfaces-pod-deleted:{fact.pod_id}",
)
facts.subscribe(
    PodMemberRemoved,
    kind=SURFACES_MEMBER_REMOVED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"surfaces-member-removed:{fact.pod_id}:{fact.user_id}",
)
