"""Functions written in a pod."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import emit
from app.modules.analytics.events.wiring import (
    ProjectionReads,
    actor_or_system,
    origin_of,
)
from app.modules.function.domain.events import (
    FunctionCreatedEvent,
)

WIRED = frozenset({"function.created"})


@projection(
    FunctionCreatedEvent.get_event_type(),
)
async def on_function_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
    if event.get("event_type") != FunctionCreatedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = FunctionCreatedEvent.model_validate(event)
        emit(
            "function.created",
            actor=actor_or_system(parsed.user_id),
            origin=origin_of(event),
            organization_id=await reads.organization_of_pod(parsed.pod_id),
            pod_id=parsed.pod_id,
            properties={"pod_id": parsed.pod_id, "function_id": parsed.function_id},
        )

    await record()
