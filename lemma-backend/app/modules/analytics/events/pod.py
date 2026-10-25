"""Pods created, joined and deleted."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import AnalyticsActor, emit
from app.modules.analytics.events.wiring import (
    ProjectionReads,
    actor_or_system,
    origin_of,
)
from app.modules.analytics.services.buckets import COUNT_EDGES, bucket, days_bucket
from app.modules.pod.domain.events import (
    PodCreatedEvent,
    PodDeletedEvent,
    PodMemberAddedEvent,
)

WIRED = frozenset({"pod.created", "pod.member_joined", "pod.deleted"})


@projection(
    PodCreatedEvent.get_event_type(),
    PodMemberAddedEvent.get_event_type(),
    PodDeletedEvent.get_event_type(),
)
async def on_pod_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
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
                properties={"pod_id": parsed.pod_id, "source": parsed.source},
            )
        elif event_type == PodMemberAddedEvent.get_event_type():
            parsed_member = PodMemberAddedEvent.model_validate(event)
            # Counted here rather than carried on the event, as the organization
            # count is: a count written at publish time is stale by the time it
            # is consumed.
            member_count = await reads.pod_member_count(parsed_member.pod_id)
            emit(
                "pod.member_joined",
                actor=AnalyticsActor.user(parsed_member.user_id),
                origin=origin,
                organization_id=await reads.organization_of_pod(parsed_member.pod_id),
                pod_id=parsed_member.pod_id,
                properties={
                    "pod_id": parsed_member.pod_id,
                    "member_count_bucket": bucket(member_count, COUNT_EDGES),
                },
            )
        else:
            parsed_deleted = PodDeletedEvent.model_validate(event)
            emit(
                "pod.deleted",
                actor=actor_or_system(parsed_deleted.deleted_by_user_id),
                origin=origin,
                organization_id=parsed_deleted.organization_id,
                pod_id=parsed_deleted.pod_id,
                properties={
                    "pod_id": parsed_deleted.pod_id,
                    "age_days_bucket": days_bucket(_age_days(parsed_deleted)),
                },
            )

    await record()


def _age_days(deleted: PodDeletedEvent) -> int | None:
    """Whole days the pod lived, from its creation to its deletion."""
    if deleted.pod_created_at is None:
        return None
    return max((deleted.occurred_at - deleted.pod_created_at).days, 0)
