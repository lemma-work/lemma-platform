"""The rest of what removing an installation stops: surfaces, schedules and
MCP event subscriptions, and the pods they are in.

``PodConnectorService.impact`` answers what is this module's own -- accounts,
pod accounts, pod connectors. Surfaces and schedules are other modules' rows,
bound to a pod connector by ``(pod, slug)``, so they are asked for through
those modules' contracts; nothing here reads their tables.

The counts are the organization's: whoever removes an installation removes it
for everyone, and should know how much goes. The pods are named only where the
caller could open them, asked of the caller's own authority in each, the way a
request of theirs to that pod would be. Everywhere else -- including a pod past
the ones checked, or every pod when the check itself failed -- the connectors
are counted, never listed: a slug says what a pod is for as plainly as its name.

It fails soft. The preview is advice before a removal, never a condition of
it: if this half cannot be read, the caller gets the connectors module's own
half with ``complete: false``, and the removal it was shown before stays
possible.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.authorization.context import ResourceRef
from app.core.domain.errors import DomainError
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.contracts.connector_usage import (
    surfaces_on_pod_connectors,
)
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    ImpactedPod,
    InstallationImpactResponse,
)
from app.modules.connectors.infrastructure.repositories.installation_impact_queries import (
    event_subscription_count,
    event_subscription_ids,
)
from app.modules.pod.contracts.user_pods import pods_by_ids
from app.modules.schedule.contracts.connector_usage import (
    schedules_on_pod_connectors,
)

logger = get_logger(__name__)

#: How many of each are read to say where they are. Past this the totals are
#: still exact for subscriptions; surfaces and schedules stop counting here.
MAX_IMPACT_ROWS = 2000

#: How many pods are checked for whether the caller can open them.
MAX_NAMED_PODS = 100

PodVisible = Callable[[SqlAlchemyUnitOfWork, UUID, UUID], Awaitable[bool]]


async def caller_can_open(
    uow: SqlAlchemyUnitOfWork, user_id: UUID, pod_id: UUID
) -> bool:
    """Whether a request of the caller's to this pod would be let in."""
    ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    return await ctx.can(Permissions.POD_READ, ResourceRef.pod(pod_id))


async def with_what_else_stops(
    uow: SqlAlchemyUnitOfWork,
    impact: InstallationImpactResponse,
    *,
    user_id: UUID,
    can_open: PodVisible = caller_can_open,
    max_named: int = MAX_NAMED_PODS,
) -> InstallationImpactResponse:
    """``impact`` with the surfaces, schedules and subscriptions added and the
    pods the caller cannot open taken out, or -- when the rest could not be
    read -- with only what was learned and marked incomplete."""
    connectors: Sequence[tuple[UUID, str]] = sorted(
        {(one.pod_id, one.slug) for one in impact.pod_connectors}
    )
    visible: frozenset[UUID] = frozenset()
    try:
        candidates = sorted({pod_id for pod_id, _slug in connectors})[:max_named]
        visible = frozenset(
            [pod_id for pod_id in candidates if await can_open(uow, user_id, pod_id)]
        )
        whole = await _what_else_stops(uow, impact, connectors, visible)
    except SQLAlchemyError, RedisError, OSError, DomainError:
        logger.warning(
            "connectors.installation_impact.partial.degraded",
            auth_config_id=str(impact.auth_config_id),
            exc_info=True,
        )
        # The preview writes nothing, so ending its transaction loses nothing,
        # and a failed statement would otherwise fail the request's commit. Not
        # a savepoint: the authorizer commits mid-check to hand its connection
        # back, which a savepoint does not survive.
        await uow.session.rollback()
        return _named_only(impact, visible).model_copy(update={"complete": False})
    return _named_only(whole, visible)


def _named_only(
    impact: InstallationImpactResponse, visible: frozenset[UUID]
) -> InstallationImpactResponse:
    """``impact`` listing only the pod connectors in ``visible`` pods; the
    others are counted."""
    named = [one for one in impact.pod_connectors if one.pod_id in visible]
    unnamed = [one for one in impact.pod_connectors if one.pod_id not in visible]
    return impact.model_copy(
        update={
            "pod_connectors": named,
            "unnamed_pod_count": len({one.pod_id for one in unnamed}),
            "unnamed_pod_connector_count": len(unnamed),
        }
    )


async def _what_else_stops(
    uow: SqlAlchemyUnitOfWork,
    impact: InstallationImpactResponse,
    connectors: Sequence[tuple[UUID, str]],
    visible: frozenset[UUID],
) -> InstallationImpactResponse:
    session = uow.session
    surfaces = await surfaces_on_pod_connectors(
        session, connectors, limit=MAX_IMPACT_ROWS
    )
    schedules = await schedules_on_pod_connectors(
        session, connectors, limit=MAX_IMPACT_ROWS
    )
    subscriptions = await event_subscription_ids(
        session, impact.auth_config_id, limit=MAX_IMPACT_ROWS
    )
    surfaces_in = Counter(one.pod_id for one in surfaces)
    schedules_in = Counter(one.pod_id for one in schedules)
    subscriptions_in = Counter(
        one.pod_id for one in schedules if one.provider_trigger_id in subscriptions
    )
    slugs_in: dict[UUID, list[str]] = defaultdict(list)
    for pod_id, slug in connectors:
        slugs_in[pod_id].append(slug)
    pods = [
        ImpactedPod(
            pod_id=pod.id,
            name=pod.name,
            pod_connectors=sorted(slugs_in[pod.id]),
            surface_count=surfaces_in[pod.id],
            schedule_count=schedules_in[pod.id],
            event_subscription_count=subscriptions_in[pod.id],
        )
        for pod in await pods_by_ids(session=session, pod_ids=sorted(visible))
    ]
    return impact.model_copy(
        update={
            "surface_count": len(surfaces),
            "schedule_count": len(schedules),
            "event_subscription_count": await event_subscription_count(
                session, impact.auth_config_id
            ),
            "pods": pods,
        }
    )


__all__ = ["MAX_IMPACT_ROWS", "caller_can_open", "with_what_else_stops"]
