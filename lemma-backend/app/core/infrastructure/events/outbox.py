"""Transactional outbox dispatcher and replay operations."""

from __future__ import annotations

import asyncio
import os
import random
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID, uuid4

from opentelemetry import metrics, trace
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.models import DomainEventOutbox
from app.core.infrastructure.events.config import event_transport_settings
from app.core.log.log import get_logger


logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
published_counter = meter.create_counter("lemma.event.outbox.published")
failed_counter = meter.create_counter("lemma.event.outbox.failed")
dead_letter_counter = meter.create_counter("lemma.event.outbox.dead_lettered")
publish_latency = meter.create_histogram("lemma.event.outbox.publish_latency_ms")


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    id: UUID
    stream: str
    event_type: str
    payload: dict[str, Any]
    attempts: int
    occurred_at: datetime


class OutboxDispatcher:
    def __init__(
        self,
        session_maker: Callable[[], AsyncSession],
        message_bus,
        *,
        batch_size: int = 100,
        max_attempts: int = 10,
        lease_seconds: int = 60,
        poll_seconds: float = 0.5,
        owner: str | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._message_bus = message_bus
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"

    async def dispatch_once(self) -> int:
        claimed = await self._claim_batch()
        for event in claimed:
            await self._publish(event)
        return len(claimed)

    async def run(self) -> None:
        logger.info("Transactional outbox dispatcher started", owner=self.owner)
        infrastructure_failures = 0
        while True:
            try:
                dispatched = await self.dispatch_once()
                infrastructure_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - long-lived process boundary
                infrastructure_failures += 1
                delay = min(30.0, 2 ** min(infrastructure_failures - 1, 5))
                delay *= random.uniform(0.75, 1.25)
                logger.warning(
                    "Outbox dispatcher infrastructure failure; retrying",
                    owner=self.owner,
                    error_type=type(exc).__name__,
                    retry_in_seconds=round(delay, 3),
                )
                await asyncio.sleep(delay)
                continue
            if dispatched == 0:
                await asyncio.sleep(self.poll_seconds)

    async def _claim_batch(self) -> list[ClaimedEvent]:
        now = datetime.now(timezone.utc)
        async with self._session_maker() as session, session.begin():
            stmt = (
                select(DomainEventOutbox)
                .where(
                    DomainEventOutbox.published_at.is_(None),
                    DomainEventOutbox.dead_lettered_at.is_(None),
                    DomainEventOutbox.available_at <= now,
                    or_(
                        DomainEventOutbox.lease_until.is_(None),
                        DomainEventOutbox.lease_until <= now,
                    ),
                )
                .order_by(DomainEventOutbox.occurred_at, DomainEventOutbox.id)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            rows = list((await session.scalars(stmt)).all())
            for row in rows:
                row.lease_owner = self.owner
                row.lease_until = now + timedelta(seconds=self.lease_seconds)
            return [
                ClaimedEvent(
                    id=row.id,
                    stream=row.stream,
                    event_type=row.event_type,
                    payload=row.payload,
                    attempts=row.attempts,
                    occurred_at=row.occurred_at,
                )
                for row in rows
            ]

    async def _publish(self, event: ClaimedEvent) -> None:
        started = asyncio.get_running_loop().time()
        with tracer.start_as_current_span("lemma.outbox.publish") as span:
            span.set_attribute("lemma.event_id", str(event.id))
            span.set_attribute("lemma.event_type", event.event_type)
            span.set_attribute("lemma.event_stream", event.stream)
            span.set_attribute("lemma.event_attempt", event.attempts + 1)
            try:
                await asyncio.wait_for(
                    self._message_bus.publish(event.stream, event.payload),
                    timeout=event_transport_settings.event_publish_timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # publication boundary; persisted for retry
                await self._mark_failed(event, exc)
                failed_counter.add(1, {"event_type": event.event_type})
                span.record_exception(exc)
                logger.warning(
                    "Outbox publication failed",
                    event_id=str(event.id),
                    event_type=event.event_type,
                    stream=event.stream,
                    attempt=event.attempts + 1,
                    error_type=type(exc).__name__,
                )
                return

            await self._mark_published(event.id)
            published_counter.add(1, {"event_type": event.event_type})
            publish_latency.record(
                (asyncio.get_running_loop().time() - started) * 1000,
                {"event_type": event.event_type},
            )

    async def _mark_published(self, event_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_maker() as session, session.begin():
            await session.execute(
                update(DomainEventOutbox)
                .where(
                    DomainEventOutbox.id == event_id,
                    DomainEventOutbox.lease_owner == self.owner,
                )
                .values(
                    published_at=now,
                    lease_owner=None,
                    lease_until=None,
                    last_error_type=None,
                    last_error=None,
                )
            )

    async def _mark_failed(self, event: ClaimedEvent, exc: Exception) -> None:
        now = datetime.now(timezone.utc)
        failed_attempts = event.attempts + 1
        terminal = failed_attempts >= self.max_attempts
        delay = min(300.0, 2 ** max(0, failed_attempts - 1))
        delay *= random.uniform(0.75, 1.25)
        values = {
            "lease_owner": None,
            "lease_until": None,
            "last_error_type": type(exc).__name__[:200],
            "last_error": "Event publication failed; inspect the correlated trace",
            "available_at": now + timedelta(seconds=delay),
            "attempts": failed_attempts,
        }
        if terminal:
            values["dead_lettered_at"] = now
            dead_letter_counter.add(1, {"event_type": event.event_type})
        async with self._session_maker() as session, session.begin():
            await session.execute(
                update(DomainEventOutbox)
                .where(
                    DomainEventOutbox.id == event.id,
                    DomainEventOutbox.lease_owner == self.owner,
                )
                .values(**values)
            )


async def replay_outbox_event(
    session_maker: Callable[[], AsyncSession], event_id: UUID
) -> bool:
    """Make a failed/dead-lettered event eligible for publication again."""
    async with session_maker() as session, session.begin():
        result = await session.execute(
            update(DomainEventOutbox)
            .where(DomainEventOutbox.id == event_id)
            .values(
                attempts=0,
                available_at=datetime.now(timezone.utc),
                lease_owner=None,
                lease_until=None,
                published_at=None,
                dead_lettered_at=None,
                last_error_type=None,
                last_error=None,
            )
        )
        return bool(cast(CursorResult[Any], result).rowcount)


@asynccontextmanager
async def outbox_dispatcher_lifespan(
    session_maker: Callable[[], AsyncSession], message_bus
) -> AsyncIterator[OutboxDispatcher]:
    dispatcher = OutboxDispatcher(session_maker, message_bus)
    task = asyncio.create_task(dispatcher.run(), name="domain-event-outbox-dispatcher")
    try:
        yield dispatcher
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
