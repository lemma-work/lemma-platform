"""Functions written in a pod."""

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
from app.modules.function.domain.events import (
    FUNCTION_EVENTS_STREAM,
    FunctionCreatedEvent,
)

WIRED = frozenset({"function.created"})


@reliable_redis_stream_subscriber(
    router,
    FUNCTION_EVENTS_STREAM,
    group="analytics-function",
    consumer="analytics-function-consumer",
)
async def on_function_event(
    event: dict[str, object],
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != FunctionCreatedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = FunctionCreatedEvent.model_validate(event)
        emit(
            "function.created",
            actor=actor_or_system(parsed.user_id),
            origin=origin_of(event),
            pod_id=parsed.pod_id,
            properties={"pod_id": parsed.pod_id, "function_id": parsed.function_id},
        )

    await inbox.process("analytics.function", event, record)
