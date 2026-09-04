"""Apps built in a pod, and apps published out of one."""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import emit
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.analytics.events.wiring import actor_or_system, origin_of, router
from app.modules.apps.domain.events import (
    APP_EVENTS_STREAM,
    AppCreatedEvent,
    AppPublishedEvent,
)

WIRED = frozenset({"app.created", "app.published"})


@reliable_redis_stream_subscriber(
    router,
    APP_EVENTS_STREAM,
    group="analytics-app",
    consumer="analytics-app-consumer",
)
async def on_app_event(
    event: dict[str, object],
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        AppCreatedEvent.get_event_type(),
        AppPublishedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == AppCreatedEvent.get_event_type():
            created = AppCreatedEvent.model_validate(event)
            emit(
                "app.created",
                actor=actor_or_system(created.user_id),
                origin=origin,
                pod_id=created.pod_id,
                properties={"pod_id": created.pod_id, "app_id": created.app_id},
            )
            return
        published = AppPublishedEvent.model_validate(event)
        emit(
            "app.published",
            actor=actor_or_system(published.user_id),
            origin=origin,
            pod_id=published.pod_id,
            properties={"pod_id": published.pod_id, "app_id": published.app_id},
        )

    await inbox.process("analytics.app", event, record)
