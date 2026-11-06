"""What every source consumer in this package shares.

Named `wiring` rather than `router` so that `events.router` can only ever
mean the object, never this module: the package binds both names, and a
submodule shadowing its own export is a bug that reads as a typo.

Every projection is registered by event type on the after-commit analytics
sink (``app.core.analytics.domain_events``): no consumer group, no inbox, and
nothing the product depends on waits behind it.

Why committed domain events and not the controllers:

* instrumentation cannot drift from behaviour, because there is nothing to keep
  in sync -- no controller mentions analytics;
* an event that fires is an event that committed, since the unit of work hands
  it to the sink only after its commit succeeds. Controller-level emits
  routinely report actions that later rolled back;
* every origin is covered by one implementation. A pod created from the CLI,
  from an agent over MCP, from a coding agent over ACP or from a workflow node
  all land on the same domain event.

``origin`` rides on the event itself (``DomainEvent.origin``), captured where
the work arrived. These projections never infer it from their own surroundings
-- they run on the sink's task, where those surroundings say nothing about the
caller.
"""

from __future__ import annotations

from uuid import UUID

from app.core.analytics import AnalyticsActor
from app.core.authorization.context import ActorType
from app.core.bounded import BoundedDict
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.origin import Origin, origin_from_payload
from app.modules.agent_surfaces.contracts.conversations import (
    surface_id_for_conversation,
)
from app.modules.analytics.services.pod_delivery import (
    DeliveryVia,
    maybe_emit_pod_delivered,
    pod_creator,
)
from app.modules.identity.contracts.organizations import organization_member_count
from app.modules.pod.contracts.agent_access import pod_organization_id
from app.modules.pod.contracts.members import pod_member_count

#: Rebuild the origin the work arrived through, from the event itself. The sink
#: binds the same value as a contextvar so projections that raise their own
#: events inherit it.
origin_of = origin_from_payload

#: Terminal statuses that count as an outcome. A failed or cancelled run is not
#: a delivery, however it arrived.
DELIVERED_STATUSES = frozenset({"COMPLETED", "SUCCEEDED"})

#: A pod never changes organization (the ``(id, organization_id)`` key surfaces
#: and grants reference pins it), so a remembered answer cannot go stale, and
#: forgetting one costs one primary-key read.
_pod_organizations: BoundedDict[UUID, UUID] = BoundedDict(
    4096, name="analytics.pod_organization", touch_on_get=True
)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def actor_or_system(actor_id: UUID | None) -> AnalyticsActor:
    """The person who did it, or the platform when the event names nobody."""
    if actor_id is None:
        return AnalyticsActor.autonomous(ActorType.SYSTEM)
    return AnalyticsActor.user(actor_id)


class ProjectionReads:
    """Everything a projection reads besides its event, behind one seam.

    A projection is handed one of these rather than reaching for each module's
    contract itself, so a test hands it its answers instead of patching the
    projection's imports.
    """

    def __init__(self, uow_factory: UnitOfWorkFactory | None = None) -> None:
        self.uow_factory = uow_factory or provide_uow_factory()

    async def organization_of_pod(self, pod_id: UUID | None) -> UUID | None:
        """The organization holding the pod.

        The catalog gives every pod-scoped event both groups, and most source
        events name only the pod. Without the organization an event is
        invisible to organization-level retention -- the unit the catalog calls
        the account -- so it is looked up rather than half the scope sent.
        """
        if pod_id is None:
            return None
        known = _pod_organizations.get(pod_id)
        if known is not None:
            return known
        organization_id = await self._read_pod_organization(pod_id)
        if organization_id is not None:
            _pod_organizations[pod_id] = organization_id
        return organization_id

    async def _read_pod_organization(self, pod_id: UUID) -> UUID | None:
        async with self.uow_factory() as uow:
            return await pod_organization_id(uow, pod_id)

    async def pod_creator(self, pod_id: UUID) -> UUID | None:
        return await pod_creator(self.uow_factory, pod_id)

    async def pod_member_count(self, pod_id: UUID) -> int:
        async with self.uow_factory() as uow:
            return await pod_member_count(uow, pod_id)

    async def organization_member_count(self, organization_id: UUID) -> int:
        async with self.uow_factory() as uow:
            return await organization_member_count(uow, organization_id)

    async def surface_of_conversation(self, conversation_id: UUID) -> UUID | None:
        async with self.uow_factory() as uow:
            return await surface_id_for_conversation(uow, conversation_id)

    async def deliver(
        self,
        *,
        pod_id: UUID,
        organization_id: UUID | None,
        via: DeliveryVia,
        origin: Origin | None,
        recipient_user_id: UUID | None,
        creator_user_id: UUID | None,
        delegated_by_user_id: UUID | None = None,
    ) -> None:
        """``pod.delivered``, if this outcome is the pod's first that counts."""
        await maybe_emit_pod_delivered(
            self.uow_factory,
            pod_id=pod_id,
            organization_id=organization_id,
            via=via,
            origin=origin,
            recipient_user_id=recipient_user_id,
            creator_user_id=creator_user_id,
            delegated_by_user_id=delegated_by_user_id,
        )


def forget_pod_organizations() -> None:
    """For tests: start from an empty memo."""
    _pod_organizations.clear()


__all__ = [
    "DELIVERED_STATUSES",
    "ProjectionReads",
    "actor_or_system",
    "forget_pod_organizations",
    "origin_of",
    "provide_uow_factory",
]
