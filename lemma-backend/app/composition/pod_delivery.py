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

Fires once per pod, ever, and that fact lives in the pod's own ``config`` JSONB
under an ``analytics`` key. A dedicated table would have been a schema migration,
an ORM model and a whole module's infrastructure package to store one timestamp
per pod -- and the pod row is already the thing the fact is about.

Not Redis alone: a flush or a failover would re-fire activation for every
established pod at once and corrupt the funnel irreversibly. Redis sits in front
as a negative cache, which is safe because a miss only ever costs a read.

The claim is a conditional ``UPDATE ... WHERE NOT (config->'analytics' ? 'delivered_at')``
that returns a row only to the caller that won it, so two workers processing two
outcomes for the same pod at the same instant still produce exactly one event.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from sqlalchemy import text

from app.core.analytics import AnalyticsActor, emit
from app.core.authorization.context import ActorType
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind

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
    insert is the claim, so only the caller whose insert returns a row emits.
    Runs inside the consumer's inbox closure, so a crash between the insert and
    the emit is covered by at-least-once redelivery — and the retry is a no-op
    because the second insert returns nothing.
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
    except Exception:  # noqa: BLE001 - a cache that is down costs a read, not correctness
        logger.debug("analytics.pod_delivered.cache_unavailable")

    already = False
    resource_count: int | None = None
    created_at = None
    async with uow_factory() as uow:
        claimed = await uow.session.scalar(
            text(
                """
                UPDATE pods
                SET config = jsonb_set(
                    coalesce(config, '{}'::jsonb),
                    '{analytics}',
                    coalesce(config->'analytics', '{}'::jsonb)
                        || jsonb_build_object('delivered_at', :now, 'via', :via),
                    true
                )
                WHERE id = :pod_id
                  AND NOT (coalesce(config->'analytics', '{}'::jsonb) ? 'delivered_at')
                RETURNING id
                """
            ),
            {
                "pod_id": pod_id,
                "now": datetime.now(timezone.utc).isoformat(),
                "via": via.value,
            },
        )
        first = claimed is not None
        if first:
            # Seed rather than announce, when the pod had already delivered
            # before any of this existed. Dating those activations to the deploy
            # would invent a platform-wide spike on a day nothing happened, and
            # the alternative -- a backfill migration -- would still miss any pod
            # dormant on the day it ran. Deciding it here, on first touch, is both
            # simpler and more accurate.
            already = await _delivered_before(uow, pod_id)
            if already:
                await uow.session.execute(
                    text(
                        """
                        UPDATE pods
                        SET config = jsonb_set(
                            config, '{analytics,seeded}', 'true'::jsonb, true
                        )
                        WHERE id = :pod_id
                        """
                    ),
                    {"pod_id": pod_id},
                )
            else:
                resource_count, created_at = await _pod_shape(uow, pod_id)
        await uow.commit()

    try:
        await redis.set(key, "1")
    except Exception:  # noqa: BLE001 - the pod row is the truth; this is a shortcut
        logger.debug("analytics.pod_delivered.cache_unavailable")

    if not first or already:
        return

    from app.composition.analytics_consumer import _bucket, _days_bucket, COUNT_EDGES

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
            "days_since_created_bucket": _days_bucket(age_days),
            "resource_count_bucket": _bucket(resource_count, COUNT_EDGES),
        },
    )


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


async def _delivered_before(uow, pod_id: UUID) -> bool:
    """Had this pod already delivered before the outcome being processed?

    ``OFFSET 1`` is the whole trick: the current outcome is already committed and
    visible by the time the consumer runs, so "more than one completed run
    exists" means one existed before this one. Runs once per pod, ever.
    """
    return bool(await uow.session.scalar(text(_PRIOR_DELIVERY_SQL), {"pod_id": pod_id}))


async def _pod_shape(uow, pod_id: UUID) -> tuple[int | None, datetime | None]:
    """How much had been built by the time the pod first delivered.

    Cross-module on purpose, which is why this lives in ``app/composition``. It
    runs once per pod for the life of the platform, so counting seven tables
    costs nothing worth optimising -- and the alternative, a denormalised counter
    maintained on every resource write for the sake of one event, would.

    Raw SQL rather than the ORM because the seven models live in seven modules
    that must not import each other; a UNION ALL of counts is the honest way to
    ask a question that is genuinely about all of them at once.
    """
    created_at = await uow.session.scalar(
        text("SELECT created_at FROM pods WHERE id = :pod_id"), {"pod_id": pod_id}
    )
    union = " UNION ALL ".join(
        f"SELECT count(*) AS n FROM {table} WHERE pod_id = :pod_id"
        for table in _RESOURCE_TABLES
    )
    total = await uow.session.scalar(
        text(f"SELECT sum(n) FROM ({union}) AS counts"), {"pod_id": pod_id}
    )
    return (int(total) if total is not None else None), created_at


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
