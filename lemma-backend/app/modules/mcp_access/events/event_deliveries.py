"""Rows added in a pod, delivered to the outside clients subscribed to them.

The datastore stream is the noisiest on the platform, so the first question is
the cheap one -- does anything outside listen to this pod at all -- and only a
pod that passes pays for the inbox and the match. Each match becomes its own
job, so one slow receiver retries alone and never holds up the rest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from faststream import Depends
from faststream.redis import RedisRouter
from streaq import StreaqRetry

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_task
from app.core.log.log import get_logger
from app.core.request_context import current_observability_context
from app.modules.datastore.domain.events import DATASTORE_EVENTS_STREAM
from app.modules.mcp_access.domain.events import RECORD_CREATED
from app.modules.mcp_access.infrastructure.rate_limit import RateLimiter
from app.modules.mcp_access.infrastructure.subscription_repository import (
    FAN_OUT_PAGE,
    EventSubscriptionRepository,
)
from app.modules.mcp_access.services.event_delivery import (
    DeliveryJob,
    EventDelivery,
    Outcome,
    SqlDeliveryLedger,
    read_record_for,
)

router = RedisRouter()
logger = get_logger(__name__)

GROUP = "mcp-access-event-deliveries"
TASK = "deliver_mcp_event"
#: The draft's bounded retry: a handful of attempts over about a quarter of an
#: hour, then the event is let go.
MAX_TRIES = 5

#: Deliveries one subscription is sent per minute. A bulk import writes rows
#: far faster than any receiver wants them; past this, a delivery waits for
#: the next window instead of arriving in the same burst. Waiting is not a
#: failed try, so it does not spend the retries.
DELIVERIES_PER_MINUTE = 120
#: How many windows one delivery may wait before it is let go, so a table
#: being written without end cannot queue deliveries without end.
MAX_DEFERRALS = 30


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


class _Enqueues(Protocol):
    async def enqueue(self, job_name: str, **kwargs: object) -> object: ...


async def fan_out_record_event(
    event: dict[str, object],
    *,
    uow_factory: UnitOfWorkFactory,
    queue: _Enqueues,
    now: datetime,
) -> int:
    """One job per subscription that wants this new row; how many.

    Each job is its own delivery, so one slow receiver retries alone. The job
    id carries the source event, so a redelivered stream entry enqueues
    nothing twice."""
    try:
        pod_id = UUID(str(event.get("pod_id")))
    except ValueError:
        return 0
    table = str(event.get("table_name") or "")
    enqueued, after = 0, None
    while True:
        async with uow_factory() as uow:
            page = await EventSubscriptionRepository(uow).live_for(
                pod_id=pod_id, name=RECORD_CREATED, table=table, now=now, after=after
            )
        for subscription in page:
            await queue.enqueue(
                TASK,
                _job_id=f"mcp-event:{subscription.public_id}:{event.get('event_id')}",
                subscription_id=subscription.public_id,
                pod_id=str(pod_id),
                table=table,
                record_id=str(event.get("record_id")),
                event_id=str(event.get("event_id")),
                occurred_at=str(event.get("occurred_at")),
            )
        enqueued += len(page)
        if len(page) < FAN_OUT_PAGE:
            return enqueued
        after = page[-1].public_id


@reliable_redis_stream_subscriber(
    router,
    DATASTORE_EVENTS_STREAM,
    group=GROUP,
    consumer="mcp-access-event-deliveries-consumer",
)
async def on_record_event(
    event: dict[str, object],
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != "datastore.record.insert":
        return
    try:
        pod_id = UUID(str(event.get("pod_id")))
    except ValueError:
        return
    now = datetime.now(timezone.utc)
    async with uow_factory() as uow:
        if not await EventSubscriptionRepository(uow).pod_is_subscribed(
            pod_id, now=now
        ):
            return

    async def fan_out() -> None:
        await fan_out_record_event(
            event, uow_factory=uow_factory, queue=get_streaq_job_queue(), now=now
        )

    await inbox.process("mcp-access.record-created", event, fan_out)


class _Limits(Protocol):
    async def retry_after(
        self, key: str, *, limit: int, window_seconds: int
    ) -> int | None: ...


async def deliver_within_rate(
    job: DeliveryJob,
    *,
    deferrals: int,
    delivery: EventDelivery,
    limiter: _Limits,
    queue: _Enqueues,
    now: datetime,
) -> Outcome | None:
    """Deliver now, or -- past the subscription's rate -- put the delivery
    off to the next window and return None. Waiting is not a failed try."""
    wait = await limiter.retry_after(
        f"event:{job.subscription_id}",
        limit=DELIVERIES_PER_MINUTE,
        window_seconds=60,
    )
    if wait is None:
        return await delivery.deliver(job)
    if deferrals >= MAX_DEFERRALS:
        logger.warning(
            "mcp_access.event_delivery.rate_limited.degraded",
            subscription_id=job.subscription_id,
        )
        return Outcome.DROPPED
    await queue.enqueue(
        TASK,
        _job_id=f"mcp-event:{job.subscription_id}:{job.event_id}:wait{deferrals + 1}",
        _defer_until=now + timedelta(seconds=wait),
        subscription_id=job.subscription_id,
        pod_id=str(job.pod_id),
        table=job.table,
        record_id=job.record_id,
        event_id=job.event_id,
        occurred_at=job.occurred_at,
        deferrals=deferrals + 1,
    )
    return None


@streaq_task(name=TASK, lane=Lane.BULK, max_tries=MAX_TRIES)
async def deliver_mcp_event(
    subscription_id: str,
    pod_id: str,
    table: str,
    record_id: str,
    event_id: str,
    occurred_at: str,
    deferrals: int = 0,
) -> None:
    outcome = await deliver_within_rate(
        DeliveryJob(
            subscription_id=subscription_id,
            pod_id=UUID(pod_id),
            table=table,
            record_id=record_id,
            event_id=event_id,
            occurred_at=occurred_at,
        ),
        deferrals=deferrals,
        delivery=EventDelivery(
            SqlDeliveryLedger(provide_uow_factory()), read_record=read_record_for
        ),
        limiter=RateLimiter(),
        queue=get_streaq_job_queue(),
        now=datetime.now(timezone.utc),
    )
    if outcome is None:
        return
    logger.info(
        "mcp_access.event_delivery.attempted",
        subscription_id=subscription_id,
        outcome=outcome.value,
    )
    if outcome is Outcome.RETRY:
        attempt = current_observability_context().job_attempt or 1
        if attempt < MAX_TRIES:
            raise StreaqRetry(delay=min(30 * attempt**2, 600))
