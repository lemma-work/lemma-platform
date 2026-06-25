"""Pod lifecycle event handlers for schedule cleanup.

Subscribes to the shared ``pod_events`` stream and, on pod deletion, tears
down every schedule that belonged to the pod (APScheduler jobs and Composio
webhook triggers included) so they can no longer fire.
"""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import redis_stream_sub
from app.modules.pod.domain.events import PodDeletedEvent, PodEvents
from app.modules.schedule.api.dependencies import get_schedule_service

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@router.subscriber(stream=redis_stream_sub(PodEvents.STREAM))
async def on_pod_deleted(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
) -> None:
    """Delete all schedules for a deleted pod.

    System-level cleanup, so it goes through the service (for full external
    teardown) but bypasses RBAC by listing every schedule in the pod directly.
    """
    if event.get("event_type") != PodDeletedEvent.get_event_type():
        return

    parsed = PodDeletedEvent.model_validate(event)
    fs_logger.info("Processing PodDeletedEvent for schedule cleanup pod=%s", parsed.pod_id)

    async with uow_factory() as uow:
        count = await get_schedule_service(uow).delete_all_for_pod(parsed.pod_id)
    fs_logger.info("Deleted %s schedules for deleted pod %s", count, parsed.pod_id)
