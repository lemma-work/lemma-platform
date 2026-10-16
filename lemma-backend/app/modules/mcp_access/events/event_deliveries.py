"""Rows added in a pod, delivered to the outside clients subscribed to them.

Each event subscription is a durable subscriber of its pod's change log,
``mcp:<public id>``, watching inserts into its table. The change log wakes it
only when that table changes and hands it pages of changes in commit order;
for each, this writes one ``mcp_event_deliveries`` row, unique on
(subscription, change), and one ``mcp.deliver`` item -- in one transaction, so
a page delivered twice owes nothing twice.

``mcp.deliver`` is a ``delivery``-class job: it re-checks that the subscription
and its connection can still receive, reads the row as the person, and sends
one signed POST. The per-subscription rate (``mcp_delivery``) is drawn when the
item is claimed, so a bulk import reaches a receiver at that pace and waits,
rather than being dropped. A receiver that keeps failing pauses its
subscription (as before). A paused subscription has no subscriber row, so it
holds none of the pod's log: the changes made while it is paused are never
sent, and the refresh that resumes it watches from then on. The draft's
delivery has no way to tell a receiver it missed some, so the resume logs
``mcp_access.event_subscription.resumed_after_pause`` with when the pause began
instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    DeadContext,
    Exponential,
    ItemFailure,
    JobContext,
    RateUse,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.contracts.changes import (
    Change,
    ChangeOp,
    DeliverOutcome,
    LapsedSpan,
    SubscriberSpec,
    register_durable_subscriber,
)
from app.modules.mcp_access.domain.events import RECORD_CREATED
from app.modules.mcp_access.infrastructure.models import (
    McpEventDelivery,
    McpEventSubscription,
)
from app.modules.mcp_access.infrastructure.subscription_repository import (
    EventSubscriptionRepository,
)
from app.modules.mcp_access.services.event_delivery import (
    DeliveryJob,
    EventDelivery,
    Outcome,
    SqlDeliveryLedger,
    read_record_for,
)

logger = get_logger(__name__)

PREFIX = "mcp"
DELIVER = "mcp.deliver"
#: The draft's bounded retry: a handful of attempts over about a quarter of an
#: hour, then the event is let go.
MAX_TRIES = 5


class DeliveryRef(BaseModel):
    delivery_id: UUID


def _change_key(change: Change) -> str:
    """The change's identity for this subscription: its log position, or for a
    change converted at cutover the old event's id, which the old delivery job
    was keyed on."""
    return str(change.event_id) if change.event_id is not None else change.position.key


class McpChangeSubscriber:
    async def spec(
        self, uow: SqlAlchemyUnitOfWork, pod_id: UUID, subscriber_id: str
    ) -> SubscriberSpec | None:
        subscription = await EventSubscriptionRepository(uow).get(subscriber_id)
        if not _listening(subscription, pod_id):
            return None
        assert subscription is not None
        if subscription.paused_at is not None:
            # Paused, it watches nothing: a row kept behind its cursor would
            # hold the pod's whole log for a receiver that may never return.
            return None
        return SubscriberSpec(
            table_name=str(subscription.arguments.get("table") or ""),
            ops=frozenset({ChangeOp.INSERT}),
        )

    async def deliver(
        self,
        uow: SqlAlchemyUnitOfWork,
        pod_id: UUID,
        subscriber_id: str,
        changes: Sequence[Change],
    ) -> DeliverOutcome:
        subscription = await EventSubscriptionRepository(uow).get(subscriber_id)
        if not _listening(subscription, pod_id):
            return DeliverOutcome.GONE
        assert subscription is not None
        if subscription.paused_at is not None:
            return DeliverOutcome.GONE
        row_id = await uow.session.scalar(
            select(McpEventSubscription.id).where(
                McpEventSubscription.public_id == subscriber_id
            )
        )
        table = str(subscription.arguments.get("table") or "")
        work = WorkWriter.of(uow)
        for change in changes:
            if change.op is not ChangeOp.INSERT:
                continue
            key = _change_key(change)
            delivery_id = await uow.session.scalar(
                insert(McpEventDelivery)
                .values(
                    subscription_id=row_id,
                    change_key=key,
                    pod_id=pod_id,
                    table_name=table,
                    record_id=change.record_id,
                    occurred_at=change.at,
                    created_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(constraint="uq_mcp_event_deliveries_change")
                .returning(McpEventDelivery.id)
            )
            if delivery_id is None:
                continue
            await work.add(
                DELIVER,
                pod_namespace(pod_id),
                DeliveryRef(delivery_id=delivery_id),
                dedup_key=f"mcp:{subscriber_id}:{key}",
                rate_key=f"mcp:{subscriber_id}",
            )
        return DeliverOutcome.ADVANCE

    async def lapsed(
        self,
        uow: SqlAlchemyUnitOfWork,
        pod_id: UUID,
        subscriber_id: str,
        skipped: LapsedSpan,
    ) -> None:
        """Logged, not sent: the draft's delivery carries one occurrence and
        has no gap notice for a receiver, so the skip is the operator's to see."""
        del uow
        logger.warning(
            "mcp_access.event_subscription.lapsed",
            subscription_id=subscriber_id,
            pod_id=str(pod_id),
            skipped=skipped.count,
        )


def _event_id(change_key: str) -> str:
    """The delivery's ``eventId`` / ``webhook-id``: the same on every retry. A
    change converted at cutover keeps the old event's id, so a receiver that
    already saw it from the old path deduplicates it."""
    return f"chg_{change_key.replace(':', '_')}" if ":" in change_key else change_key


def _listening(subscription, pod_id: UUID) -> bool:
    """Exists, in this pod, for new rows, verified, not stopped, not lapsed."""
    return (
        subscription is not None
        and subscription.pod_id == pod_id
        and subscription.name == RECORD_CREATED
        and subscription.verified_at is not None
        and subscription.stopped_at is None
        and subscription.refresh_before > datetime.now(timezone.utc)
    )


async def run_deliver(ctx: JobContext, payload: DeliveryRef) -> None:
    async with ctx.checkpoint() as uow:
        delivery = await uow.session.get(McpEventDelivery, payload.delivery_id)
        public_id = (
            None
            if delivery is None
            else await uow.session.scalar(
                select(McpEventSubscription.public_id).where(
                    McpEventSubscription.id == delivery.subscription_id
                )
            )
        )
        job = (
            None
            if delivery is None
            or delivery.delivered_at is not None
            or public_id is None
            else DeliveryJob(
                subscription_id=public_id,
                pod_id=delivery.pod_id,
                table=delivery.table_name,
                record_id=delivery.record_id,
                event_id=_event_id(delivery.change_key),
                occurred_at=delivery.occurred_at.isoformat(),
            )
        )
    if job is None:
        return
    outcome = await EventDelivery(
        SqlDeliveryLedger(SessionUnitOfWorkFactory(async_session_maker)),
        read_record=read_record_for,
    ).deliver(job)
    logger.info(
        "mcp_access.event_delivery.attempted",
        subscription_id=job.subscription_id,
        outcome=outcome.value,
    )
    if outcome is Outcome.RETRY:
        raise ItemFailure(f"receiver did not take delivery {payload.delivery_id}")
    async with ctx.complete_work() as uow:
        await _settle(uow, payload.delivery_id, outcome.value)


async def on_deliver_dead(ctx: DeadContext) -> None:
    """Out of tries: the event is let go, and says so."""
    try:
        payload = DeliveryRef.model_validate(ctx.item.payload)
    except ValueError:
        return
    await _settle(ctx.uow, payload.delivery_id, Outcome.DROPPED.value)


async def _settle(uow: SqlAlchemyUnitOfWork, delivery_id: UUID, outcome: str) -> None:
    await uow.session.execute(
        update(McpEventDelivery)
        .where(McpEventDelivery.id == delivery_id)
        .values(delivered_at=datetime.now(timezone.utc), outcome=outcome)
    )


def register_mcp_delivery_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=DELIVER,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=DeliveryRef,
        retry=RetryPolicy(
            max_attempts=MAX_TRIES,
            backoff=Exponential(
                base=timedelta(seconds=30), cap=timedelta(minutes=10), jitter=0.2
            ),
        ),
        on_dead=on_deliver_dead,
        rates=[RateUse("mcp_delivery", per_key=True)],
    )(run_deliver)


register_mcp_delivery_work(default_registry)
register_durable_subscriber(PREFIX, McpChangeSubscriber())

__all__ = [
    "DELIVER",
    "PREFIX",
    "DeliveryRef",
    "McpChangeSubscriber",
    "register_mcp_delivery_work",
]
