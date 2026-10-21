"""Sign-ups, organizations, and organizations growing."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import AnalyticsActor, emit
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.analytics.events.wiring import (
    actor_or_system,
    origin_of,
    provide_uow_factory,
)
from app.modules.analytics.services.buckets import COUNT_EDGES, bucket
from app.modules.identity.contracts.organizations import organization_member_count
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
    uow_factory: UnitOfWorkFactory | None = None,
) -> None:
    uow_factory = uow_factory or provide_uow_factory()
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
            async with uow_factory() as uow:
                member_count = await organization_member_count(
                    uow, parsed_member.organization_id
                )
            emit(
                "organization.member_joined",
                actor=AnalyticsActor.user(parsed_member.user_id),
                origin=origin,
                organization_id=parsed_member.organization_id,
                properties={"member_count_bucket": bucket(member_count, COUNT_EDGES)},
            )

    await record()
