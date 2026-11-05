"""Sign-ups, organizations, and organizations growing."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import AnalyticsActor, emit
from app.modules.analytics.events.wiring import (
    ProjectionReads,
    actor_or_system,
    origin_of,
)
from app.modules.analytics.services.buckets import COUNT_EDGES, bucket
from app.modules.identity.domain.events import (
    OrganizationCreatedEvent,
    OrganizationMemberAddedEvent,
    UserSignedUpEvent,
)

WIRED = frozenset(
    {"auth.signed_up", "organization.created", "organization.member_joined"}
)


@projection(
    UserSignedUpEvent.get_event_type(),
    OrganizationCreatedEvent.get_event_type(),
    OrganizationMemberAddedEvent.get_event_type(),
)
async def on_identity_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
    event_type = event.get("event_type")
    if event_type not in {
        UserSignedUpEvent.get_event_type(),
        OrganizationCreatedEvent.get_event_type(),
        OrganizationMemberAddedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == UserSignedUpEvent.get_event_type():
            parsed = UserSignedUpEvent.model_validate(event)
            # Note what is *not* forwarded: the event carries email and
            # first_name, and neither is in the allowlist for auth.signed_up.
            emit(
                "auth.signed_up",
                actor=AnalyticsActor.user(parsed.user_id),
                origin=origin,
                properties={"method": parsed.method},
            )
        elif event_type == OrganizationCreatedEvent.get_event_type():
            parsed_org = OrganizationCreatedEvent.model_validate(event)
            emit(
                "organization.created",
                actor=actor_or_system(parsed_org.created_by_user_id),
                origin=origin,
                organization_id=parsed_org.organization_id,
            )
        else:
            parsed_member = OrganizationMemberAddedEvent.model_validate(event)
            # Counted here rather than carried on the event: a count written at
            # publish time is already stale by the time it is consumed.
            member_count = await reads.organization_member_count(
                parsed_member.organization_id
            )
            emit(
                "organization.member_joined",
                actor=AnalyticsActor.user(parsed_member.user_id),
                origin=origin,
                organization_id=parsed_member.organization_id,
                properties={"member_count_bucket": bucket(member_count, COUNT_EDGES)},
            )

    await record()
