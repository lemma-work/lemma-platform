"""Activation: the first time a pod produced an outcome that counts.

``pod.delivered`` is the metric the whole product-analytics design is named for,
and it is the one that cannot be read off a single domain event. A pod delivers
when **either**:

**(a) somebody other than its builder received a successful outcome.** A
completed agent run, workflow run or surface answer whose recipient is not the
pod's creator. This is what stops a builder poking their own pod from counting
as adoption.

**(b) an autonomous origin produced a successful outcome**, whoever owns it --
``SCHEDULE``, ``DATA_TRIGGER`` or ``CONNECTOR``, terminal status completed.

Branch (b) is load-bearing and easy to leave out. Without it a scheduled report
pod, owned and read by the person who built it, never activates -- and that is
the design doc's own canonical example of a pod delivering value. A definition
that scores it as churn is measuring chat, not the product.

Fires once per pod, ever. That fact is a marker on the pod's own row, claimed
through ``pod.contracts.delivery``: pod owns the write because pod owns the
column and the conditional ``UPDATE`` that makes the claim atomic. What the
marker *means* is here.

Not Redis alone: a flush or a failover would re-fire activation for every
established pod at once and corrupt the funnel irreversibly. Redis sits in front
as a negative cache, which is safe because a miss only ever costs a read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.analytics import AnalyticsActor, emit
from app.core.authorization.context import ActorType
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind
from app.modules.analytics.services.buckets import COUNT_EDGES, bucket, days_bucket
from app.modules.pod.contracts.delivery import (
    claim_first_delivery,
    mark_delivery_seeded,
    pod_provenance,
)

logger = get_logger(__name__)


class DeliveryVia(StrEnum):
    """Which kind of outcome got there first. A closed set: this is a dimension
    on a funnel, not a free-text note."""

    AGENT_RUN = "agent_run"
    WORKFLOW_RUN = "workflow_run"
    SCHEDULE_RUN = "schedule_run"
    SURFACE_MESSAGE = "surface_message"


#: Origins that deliver on their own, with no recipient test. Work that arrived
#: this way had no person waiting on it by definition.
AUTONOMOUS_ORIGINS = frozenset(
    {OriginKind.SCHEDULE, OriginKind.DATA_TRIGGER, OriginKind.CONNECTOR}
)

_CACHE_KEY = "analytics:pod-delivered:{pod_id}"


async def pod_creator(uow_factory, pod_id: UUID) -> UUID | None:
    """Who built the pod — the person an outcome has to reach *past* to count."""
    async with uow_factory() as uow:
        provenance = await pod_provenance(uow, pod_id)
    return provenance.creator_user_id if provenance else None


def qualifies(
    *,
    origin: Origin | None,
    recipient_user_id: UUID | None,
    creator_user_id: UUID | None,
) -> bool:
    """Whether this outcome is a delivery at all — branches (a) and (b) above."""
    if origin is not None and origin.kind in AUTONOMOUS_ORIGINS:
        return True
    if recipient_user_id is None or creator_user_id is None:
        # Cannot show it reached somebody else, so do not claim it did.
        return False
    return recipient_user_id != creator_user_id


async def maybe_emit_pod_delivered(
    uow_factory,
    *,
    pod_id: UUID,
    organization_id: UUID | None,
    via: DeliveryVia,
    origin: Origin | None,
    recipient_user_id: UUID | None,
    creator_user_id: UUID | None,
) -> None:
    """Emit ``pod.delivered`` if this is the first qualifying outcome for the pod.

    Safe to call on every outcome, and safe to call twice for the same one: the
    claim is the lock, so only the caller it returns ``True`` to emits. Runs
    inside the consumer's inbox closure, so a crash between the claim and the
    emit is covered by at-least-once redelivery -- and the retry is a no-op
    because the second claim returns ``False``.
    """
    if not qualifies(
        origin=origin,
        recipient_user_id=recipient_user_id,
        creator_user_id=creator_user_id,
    ):
        return

    from app.core.infrastructure.redis.client import get_redis

    key = _CACHE_KEY.format(pod_id=pod_id)
    redis = get_redis()
    try:
        if await redis.get(key):
            return
    except RedisError, OSError:
        # A cache that is down costs a read, not correctness: the claim below is
        # the only thing that decides whether this pod has already delivered.
        logger.debug("analytics.pod_delivered.cache_unavailable")

    seeded = False
    resource_count: int | None = None
    created_at: datetime | None = None
    async with uow_factory() as uow:
        first = await claim_first_delivery(
            uow, pod_id, at=datetime.now(timezone.utc), via=via.value
        )
        if first:
            # Seed rather than announce, when the pod had already delivered
            # before any of this existed. Dating those activations to the deploy
            # would invent a platform-wide spike on a day nothing happened, and
            # the alternative -- a backfill migration -- would still miss any pod
            # dormant on the day it ran. Deciding it here, on first touch, is both
            # simpler and more accurate.
            seeded = await _delivered_before(uow, pod_id)
            if seeded:
                await mark_delivery_seeded(uow, pod_id)
            else:
                resource_count = await _resource_count(uow, pod_id)
                provenance = await pod_provenance(uow, pod_id)
                created_at = provenance.created_at if provenance else None
        await uow.commit()

    try:
        await redis.set(key, "1")
    except RedisError, OSError:
        # The pod row is the truth; this is a shortcut.
        logger.debug("analytics.pod_delivered.cache_unavailable")

    if not first or seeded:
        return

    age_days = None
    if created_at is not None:
        age_days = (datetime.now(timezone.utc) - created_at).days
    emit(
        "pod.delivered",
        actor=(
            AnalyticsActor.user(recipient_user_id)
            if recipient_user_id
            else AnalyticsActor.autonomous(ActorType.SYSTEM)
        ),
        origin=origin,
        organization_id=organization_id,
        pod_id=pod_id,
        properties={
            "pod_id": pod_id,
            "via": via.value,
            "days_since_created_bucket": days_bucket(age_days),
            "resource_count_bucket": bucket(resource_count, COUNT_EDGES),
        },
    )


# -- the one place analytics reads other modules' tables directly -------------
#
# Both statements below are raw SQL against tables analytics does not own, and
# that is deliberate rather than unfinished. Each is one question that is
# genuinely about every module at once, each runs at most once per pod for the
# life of the platform, and the alternative -- a contract call per module -- is
# five or seven round-trips to answer a question the database answers in one.
#
# The dependency is therefore on these table and column names, and nothing else:
#
#   agent_runs.status, agent_runs.conversation_id, agent_conversations.pod_id
#   workflow_flow_runs.status, workflow_flow_runs.pod_id
#   schedule_runs.status, schedule_runs.schedule_id, schedules.pod_id
#   datastore_tables.pod_id, datastore_files.pod_id, functions.pod_id,
#   agents.pod_id, workflow_flows.pod_id, schedules.pod_id, apps.pod_id
#
# A rename in any of those modules breaks this file and nothing else, which is
# the cost of the exception. It is stated here so the next reader does not have
# to infer it from the SQL.

#: Whether the pod had produced a completed outcome before the one being
#: processed. Each source reaches the pod differently -- an agent run through its
#: conversation, a schedule run through its schedule, only a workflow run
#: directly -- which is why this is three subqueries rather than one.
_PRIOR_DELIVERY_SQL = """
SELECT EXISTS (
    SELECT 1 FROM agent_runs r
    JOIN agent_conversations c ON c.id = r.conversation_id
    WHERE c.pod_id = :pod_id AND upper(r.status::text) IN ('COMPLETED', 'SUCCEEDED')
    OFFSET 1
) OR EXISTS (
    SELECT 1 FROM workflow_flow_runs r
    WHERE r.pod_id = :pod_id AND upper(r.status::text) IN ('COMPLETED', 'SUCCEEDED')
    OFFSET 1
) OR EXISTS (
    SELECT 1 FROM schedule_runs r
    JOIN schedules s ON s.id = r.schedule_id
    WHERE s.pod_id = :pod_id AND upper(r.status::text) IN ('COMPLETED', 'SUCCEEDED')
    OFFSET 1
)
"""

#: The resources a pod is made of. Counted together as one shape number, never
#: reported per-type: the question is "how much had they built", not which
#: feature they used.
_RESOURCE_TABLES = (
    "datastore_tables",
    "datastore_files",
    "functions",
    "agents",
    "workflow_flows",
    "schedules",
    "apps",
)


async def _delivered_before(uow, pod_id: UUID) -> bool:
    """Had this pod already delivered before the outcome being processed?

    ``OFFSET 1`` is the whole trick: the current outcome is already committed and
    visible by the time the consumer runs, so "more than one completed run
    exists" means one existed before this one. Runs once per pod, ever.
    """
    return bool(await uow.session.scalar(text(_PRIOR_DELIVERY_SQL), {"pod_id": pod_id}))


async def _resource_count(uow, pod_id: UUID) -> int | None:
    """How much had been built by the time the pod first delivered.

    Runs once per pod for the life of the platform, so counting seven tables
    costs nothing worth optimising -- and the alternative, a denormalised counter
    maintained on every resource write for the sake of one event, would.
    """
    union = " UNION ALL ".join(
        f"SELECT count(*) AS n FROM {table} WHERE pod_id = :pod_id"
        for table in _RESOURCE_TABLES
    )
    total = await uow.session.scalar(
        text(f"SELECT sum(n) FROM ({union}) AS counts"), {"pod_id": pod_id}
    )
    return int(total) if total is not None else None


__all__ = [
    "AUTONOMOUS_ORIGINS",
    "DeliveryVia",
    "maybe_emit_pod_delivered",
    "pod_creator",
    "qualifies",
]
