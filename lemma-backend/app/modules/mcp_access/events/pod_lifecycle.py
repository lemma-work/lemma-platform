"""A deleted pod takes its connected clients with it.

Deleting a pod is a soft delete, and memberships survive it, so nothing about
the person's standing changes: every grant on the pod would go on verifying.
The verifier and the refresh path refuse a deleted pod on their own; this ends
the grants too, so the connections leave the person's list and their tokens
leave the table, rather than waiting out the 180-day ceiling.
"""

from __future__ import annotations

from datetime import datetime, timezone

from faststream import Depends
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.log.log import get_logger
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository
from app.modules.pod.domain.events import PodDeletedEvent, PodEvents

router = RedisRouter()
logger = get_logger(__name__)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="mcp-access-pod-events",
    consumer="mcp-access-pod-events-consumer",
)
async def on_pod_deleted(
    event: dict[str, object],
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != PodDeletedEvent.get_event_type():
        return

    async def end_grants() -> None:
        parsed = PodDeletedEvent.model_validate(event)
        async with uow_factory() as uow:
            ended = await McpAccessRepository(uow).revoke_pod_grants(
                pod_id=parsed.pod_id, now=datetime.now(timezone.utc)
            )
            await uow.commit()
        if ended:
            logger.info(
                "mcp_access.grants.ended_with_pod",
                pod_id=str(parsed.pod_id),
                count=ended,
            )

    await inbox.process("mcp-access.pod-deletion", event, end_grants)
