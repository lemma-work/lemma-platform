"""Apps built in a pod, and apps published out of one."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import emit
from app.modules.analytics.events.wiring import (
    ProjectionReads,
    actor_or_system,
    origin_of,
)
from app.modules.apps.domain.events import (
    AppCreatedEvent,
    AppPublishedEvent,
)

WIRED = frozenset({"app.created", "app.published"})


@projection(
    AppCreatedEvent.get_event_type(),
    AppPublishedEvent.get_event_type(),
)
async def on_app_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
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
                organization_id=await reads.organization_of_pod(created.pod_id),
                pod_id=created.pod_id,
                properties={"pod_id": created.pod_id, "app_id": created.app_id},
            )
            return
        published = AppPublishedEvent.model_validate(event)
        emit(
            "app.published",
            actor=actor_or_system(published.user_id),
            origin=origin,
            organization_id=await reads.organization_of_pod(published.pod_id),
            pod_id=published.pod_id,
            properties={"pod_id": published.pod_id, "app_id": published.app_id},
        )

    await record()
