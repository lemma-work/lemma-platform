"""Pods created, joined and deleted."""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import AnalyticsActor, emit
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.analytics.events.wiring import actor_or_system, origin_of, router
from app.modules.pod.domain.events import (
    POD_EVENTS_STREAM,
    PodCreatedEvent,
    PodDeletedEvent,
    PodMemberAddedEvent,
)

WIRED = frozenset({"pod.created", "pod.member_joined", "pod.deleted"})


@reliable_redis_stream_subscriber(
    router,
    POD_EVENTS_STREAM,
    group="analytics-pod",
    consumer="analytics-pod-consumer",
)
async def on_pod_event(
    event: dict[str, object],
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        PodCreatedEvent.get_event_type(),
        PodMemberAddedEvent.get_event_type(),
        PodDeletedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == PodCreatedEvent.get_event_type():
            parsed = PodCreatedEvent.model_validate(event)
            emit(
                "pod.created",
                actor=AnalyticsActor.user(parsed.creator_id),
                origin=origin,
                organization_id=parsed.organization_id,
                pod_id=parsed.pod_id,
                properties={"pod_id": parsed.pod_id},
            )
        elif event_type == PodMemberAddedEvent.get_event_type():
            parsed_member = PodMemberAddedEvent.model_validate(event)
            emit(
                "pod.member_joined",
                actor=AnalyticsActor.user(parsed_member.user_id),
                origin=origin,
                pod_id=parsed_member.pod_id,
                properties={"pod_id": parsed_member.pod_id},
            )
        else:
            parsed_deleted = PodDeletedEvent.model_validate(event)
            emit(
                "pod.deleted",
                actor=actor_or_system(parsed_deleted.deleted_by_user_id),
                origin=origin,
                organization_id=parsed_deleted.organization_id,
                pod_id=parsed_deleted.pod_id,
                properties={"pod_id": parsed_deleted.pod_id},
            )

    await inbox.process("analytics.pod", event, record)
