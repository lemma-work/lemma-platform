"""One event, delivered to one subscriber -- or, for a good reason, not.

Every delivery asks again what subscribing asked once: is the connection still
live, can it still read, and can the person it acts for read *this* row. OpenAI
leaves revocation to the server, and ChatGPT never hears `terminated`, so a
revoked connection or a row the person may no longer see has to be stopped
here, at the last moment there is.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import UUID

from app.core.crypto.factory import get_secret_cipher
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.mcp_access.domain.entities import Scope, parse_scopes
from app.modules.mcp_access.domain.events import MAX_PAYLOAD_BYTES, RECORD_CREATED
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository
from app.modules.mcp_access.infrastructure.subscription_repository import (
    EventSubscriptionRepository,
    StoredSubscription,
)
from app.modules.mcp_access.infrastructure.webhook_sender import (
    SendResult,
    send_signed,
)

logger = get_logger(__name__)

#: The row as this person may read it, or None when they may not.
RecordReader = Callable[[UUID, UUID, str, str], Awaitable[dict[str, object] | None]]
Sender = Callable[..., Awaitable[SendResult]]
Clock = Callable[[], datetime]


class Outcome(Enum):
    SENT = "sent"
    #: Not delivered, and never will be: expired, revoked, not visible, refused.
    DROPPED = "dropped"
    #: The receiver did not take it; try again later.
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    subscription_id: str
    pod_id: UUID
    table: str
    record_id: str
    #: The source event's id: the delivery's `eventId` and `webhook-id`, the
    #: same on every retry, which is what the receiver deduplicates on.
    event_id: str
    occurred_at: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def occurrence_body(job: DeliveryJob, record: dict[str, object]) -> bytes:
    """The draft's `EventOccurrence`, serialized once: the signature covers
    these exact bytes. A row too large for the draft's ceiling is sent as its
    id alone -- the receiver can still fetch it with the pod's tools."""

    def encode(row: dict[str, object]) -> bytes:
        return json.dumps(
            {
                "eventId": job.event_id,
                "name": RECORD_CREATED,
                "timestamp": job.occurred_at,
                "data": {"table": job.table, "record_id": job.record_id, "record": row},
                "cursor": None,
            },
            separators=(",", ":"),
            default=str,
        ).encode()

    body = encode(record)
    if len(body) > MAX_PAYLOAD_BYTES:
        body = encode({"id": job.record_id})
    return body


def outcome_of(result: SendResult) -> Outcome:
    """What the receiver's answer means for this event.

    2xx is delivered. 410 is the receiver ending the subscription and 413 a
    body it will never take: retrying either changes nothing. A URL the guard
    now refuses (it resolved somewhere private since subscribing) is not
    retried into. Everything else -- 5xx, 429, a timeout, a refused
    connection -- is worth another try.
    """
    status = result.status
    if status is not None and 200 <= status < 300:
        return Outcome.SENT
    if status in (410, 413) or (result.failure or "").startswith("unsafe_url"):
        return Outcome.DROPPED
    return Outcome.RETRY


class DeliveryLedger(Protocol):
    async def still_wanted(self, job: DeliveryJob) -> StoredSubscription | None: ...

    async def record(
        self, subscription: StoredSubscription, result: SendResult
    ) -> None: ...


class SqlDeliveryLedger:
    """The two database steps around a send, each in its own short unit of
    work, so no connection is held while the receiver answers."""

    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Clock = _utcnow) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def still_wanted(self, job: DeliveryJob) -> StoredSubscription | None:
        now = self._clock()
        async with self._uow_factory() as uow:
            subscriptions = EventSubscriptionRepository(uow)
            subscription = await subscriptions.get(job.subscription_id)
            if subscription is None or subscription.refresh_before <= now:
                return None
            scopes = await McpAccessRepository(uow).live_grant_scopes(
                subscription.grant_id
            )
            if scopes is None or Scope.READ not in parse_scopes(scopes):
                # The connection ended or lost read access: the subscription
                # ends with it, rather than lingering until its refresh lapses.
                await subscriptions.remove(subscription.public_id)
                await uow.commit()
                return None
        return subscription

    async def record(
        self, subscription: StoredSubscription, result: SendResult
    ) -> None:
        async with self._uow_factory() as uow:
            subscriptions = EventSubscriptionRepository(uow)
            if result.status == 410:
                # Gone: the receiver says this subscription is over.
                await subscriptions.remove(subscription.public_id)
            else:
                delivered = outcome_of(result) is Outcome.SENT
                await subscriptions.record_delivery(
                    subscription.public_id,
                    now=self._clock(),
                    error=None
                    if delivered
                    else (result.failure or f"http_{result.status}"),
                )
            await uow.commit()


class EventDelivery:
    def __init__(
        self,
        ledger: DeliveryLedger,
        *,
        read_record: RecordReader,
        send: Sender = send_signed,
    ) -> None:
        self._ledger = ledger
        self._read_record = read_record
        self._send = send

    async def deliver(self, job: DeliveryJob) -> Outcome:
        subscription = await self._ledger.still_wanted(job)
        if subscription is None:
            return Outcome.DROPPED
        record = await self._read_record(
            job.pod_id, subscription.user_id, job.table, job.record_id
        )
        if record is None:
            return Outcome.DROPPED
        secret = get_secret_cipher().decrypt_str(subscription.secret_ciphertext)
        if not secret:
            return Outcome.DROPPED
        result = await self._send(
            url=subscription.url,
            secret=secret,
            message_id=job.event_id,
            subscription_id=subscription.public_id,
            body=occurrence_body(job, record),
        )
        await self._ledger.record(subscription, result)
        return outcome_of(result)


async def read_record_for(
    pod_id: UUID, user_id: UUID, table: str, record_id: str
) -> dict[str, object] | None:
    """The production `RecordReader`: the row, read as the person."""
    from app.core.authorization.factory import create_authorization_data_service
    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.datastore.contracts.event_reads import read_record_as

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        return await read_record_as(
            uow,
            pod_id=pod_id,
            table_name=table,
            record_id=record_id,
            user_id=user_id,
            ctx=ctx,
        )
