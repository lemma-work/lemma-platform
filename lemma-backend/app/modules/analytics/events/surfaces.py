"""Surfaces somebody connected an agent to."""

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
from app.modules.agent_surfaces.domain.events import (
    SurfaceConnectedEvent,
    SurfaceEvents,
)
from app.modules.analytics.events.wiring import actor_or_system, origin_of, router

WIRED = frozenset({"surface.connected"})

#: Every agent gets a Resend mailbox provisioned automatically at creation, so
#: counting those as somebody connecting a surface would make inside reach track
#: the agent count instead of adoption.
_AUTO_PROVISIONED_SURFACES = frozenset({"RESEND"})


@reliable_redis_stream_subscriber(
    router,
    SurfaceEvents.STREAM,
    group="analytics-surface",
    consumer="analytics-surface-consumer",
)
async def on_surface_event(
    event: dict[str, object],
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != SurfaceConnectedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = SurfaceConnectedEvent.model_validate(event)
        if parsed.platform.upper() in _AUTO_PROVISIONED_SURFACES:
            return
        emit(
            "surface.connected",
            actor=actor_or_system(parsed.connected_by_user_id),
            origin=origin_of(event),
            pod_id=parsed.pod_id,
            properties={
                "pod_id": parsed.pod_id,
                "surface_id": parsed.surface_id,
            },
        )

    await inbox.process("analytics.surface", event, record)
