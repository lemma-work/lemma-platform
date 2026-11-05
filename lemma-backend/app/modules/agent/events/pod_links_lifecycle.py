"""A deleted pod takes its links with it.

A link is a grant to ``POD:<id>`` in the pod that gave it, and a grant names its
grantee by id with no foreign key, so nothing else would remove the links a
deleted pod held. They could never be used -- a deleted pod runs nothing -- but
they would go on listing it in "Works with" and in the brief of every pod that
let it in.

A FAST work item written by the deletion itself, under the organization's
namespace: one statement on the main database, committed with the item's
removal, so it runs exactly once.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.infrastructure.pod_link_queries import PodLinkQueries
from app.modules.pod.contracts.lifecycle import PodDeleted

AGENT_POD_LINKS_POD_DELETED = "agent.pod_links.pod_deleted"


class PodLinksPodDeletedPayload(BaseModel):
    pod_id: UUID


async def forget_deleted_pod_links(
    ctx: FastContext, payload: PodLinksPodDeletedPayload
) -> None:
    await PodLinkQueries(ctx.uow).forget_pod(pod_id=payload.pod_id)


def register_pod_links_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_POD_LINKS_POD_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PodLinksPodDeletedPayload,
    )(forget_deleted_pod_links)


register_pod_links_work(default_registry)
facts.subscribe(
    PodDeleted,
    kind=AGENT_POD_LINKS_POD_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"agent-pod-links-pod-deleted:{fact.pod_id}",
)
