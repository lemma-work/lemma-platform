"""A deleted pod takes its connected clients with it.

Deleting a pod is a soft delete, and memberships survive it, so nothing about
the person's standing changes: every grant on the pod would go on verifying.
The verifier and the refresh path refuse a deleted pod on their own; this ends
the grants too, so the connections leave the person's list and their tokens
leave the table, rather than waiting out the 180-day ceiling.

A FAST work item written by the deletion, under the organization's namespace:
main-database statements only, committed with the item's removal.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
from app.core.log.log import get_logger
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository
from app.modules.pod.contracts.lifecycle import PodDeleted

logger = get_logger(__name__)

MCP_ACCESS_POD_DELETED = "mcp_access.pod_deleted"


class McpAccessPodDeletedPayload(BaseModel):
    pod_id: UUID


async def end_deleted_pod_grants(
    ctx: FastContext, payload: McpAccessPodDeletedPayload
) -> None:
    ended = await McpAccessRepository(ctx.uow).revoke_pod_grants(
        pod_id=payload.pod_id, now=datetime.now(timezone.utc)
    )
    if ended:
        logger.info(
            "mcp_access.grants.ended_with_pod",
            pod_id=str(payload.pod_id),
            count=ended,
        )


def register_mcp_access_pod_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=MCP_ACCESS_POD_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=McpAccessPodDeletedPayload,
    )(end_deleted_pod_grants)


register_mcp_access_pod_work(default_registry)
facts.subscribe(
    PodDeleted,
    kind=MCP_ACCESS_POD_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"mcp-access-pod-deleted:{fact.pod_id}",
)
