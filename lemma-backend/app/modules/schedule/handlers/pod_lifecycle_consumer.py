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
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.log.log import get_logger
from app.modules.pod.domain.events import PodDeletedEvent, PodEvents
from app.modules.schedule.api.dependencies import get_schedule_service

router = RedisRouter()
logger = get_logger(__name__)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="schedule-pod-events",
    consumer="schedule-pod-events-consumer",
)
async def on_pod_deleted(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    """Delete all schedules for a deleted pod.

    System-level cleanup, so it goes through the service (for full external
    teardown) but bypasses RBAC by listing every schedule in the pod directly.
    """
    if event.get("event_type") != PodDeletedEvent.get_event_type():
        return

    async def delete_schedules() -> None:
        parsed = PodDeletedEvent.model_validate(event)

        async with uow_factory() as uow:
            await get_schedule_service(uow).delete_all_for_pod(parsed.pod_id)

    await inbox.process("schedule-pod-events", event, delete_schedules)
