"""Durable per-consumer idempotency and terminal-outcome tracking."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, UUID, uuid5

from opentelemetry import metrics, trace
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.models import DomainEventInbox
from app.core.log.log import get_logger
from app.core.request_context import event_lineage


logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
retry_counter = meter.create_counter("lemma.event.inbox.retry")
dead_letter_counter = meter.create_counter("lemma.event.inbox.dead_lettered")


class InboxStatus(StrEnum):
    PROCESSING = "PROCESSING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    TERMINAL = "TERMINAL"
    DEAD_LETTER = "DEAD_LETTER"


@runtime_checkable
class EventInboxPort(Protocol):
    async def process(
        self,
        consumer: str,
        event: BaseModel | Mapping[str, Any],
        handler: Callable[[], Awaitable[None]],
    ) -> bool: ...


def normalized_event_payload(event: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(event, BaseModel):
        return event.model_dump(mode="json")
    return dict(event)


def stable_event_id(event: BaseModel | Mapping[str, Any]) -> UUID:
    """Return the envelope id or a deterministic rolling-deployment fallback."""
    payload = normalized_event_payload(event)
    candidate = (
        payload.get("event_id")
        or payload.get("source_event_id")
        or payload.get("message_id")
        or payload.get("id")
    )
    if candidate:
        try:
            return UUID(str(candidate))
        except ValueError:
            return uuid5(NAMESPACE_URL, f"lemma-domain-event:{candidate}")

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return uuid5(NAMESPACE_URL, f"lemma-legacy-event:{digest}")


def _event_type(event: BaseModel | Mapping[str, Any]) -> str:
    payload = normalized_event_payload(event)
    value = payload.get("event_type")
    return str(value or type(event).__name__)


class InboxConsumer:
    """Claim, execute, and durably classify one consumer delivery.

    The inbox transaction is deliberately short. Resulting external jobs must
    use deterministic ids because a process can still die after the external
    side effect and before the completion update.
    """

    def __init__(
        self,
        session_maker: Callable[[], AsyncSession],
        *,
        max_attempts: int = 10,
        abandon_after_seconds: int = 60,
    ) -> None:
        self._session_maker = session_maker
        self.max_attempts = max_attempts
        self.abandon_after = timedelta(seconds=abandon_after_seconds)

    async def process(
        self,
        consumer: str,
        event: BaseModel | Mapping[str, Any],
        handler: Callable[[], Awaitable[None]],
    ) -> bool:
        event_id = stable_event_id(event)
        event_type = _event_type(event)
        attempt = await self._claim(consumer, event_id, event_type)
        if attempt is None:
            return False

        with tracer.start_as_current_span("lemma.inbox.consume") as span:
            span.set_attribute("lemma.event_id", str(event_id))
            span.set_attribute("lemma.event_type", event_type)
            span.set_attribute("lemma.event_consumer", consumer)
            span.set_attribute("lemma.event_attempt", attempt)
            try:
                payload = normalized_event_payload(event)
                raw_correlation_id = payload.get("correlation_id")
                try:
                    correlation_id = UUID(str(raw_correlation_id))
                except TypeError, ValueError:
                    correlation_id = event_id
                with event_lineage(
                    correlation_id=correlation_id,
                    causation_id=event_id,
                ):
                    await handler()
            except asyncio.CancelledError:
                raise
            except ValidationError as exc:
                await self._finish(
                    consumer,
                    event_id,
                    InboxStatus.TERMINAL,
                    error_type=type(exc).__name__,
                )
                logger.warning(
                    "Terminal event validation failure",
                    consumer=consumer,
                    event_id=str(event_id),
                    event_type=event_type,
                )
                return True
            except DomainError as exc:
                if exc.status_code == 503:
                    return await self._retry_or_dead_letter(
                        consumer, event_id, event_type, attempt, exc
                    )
                await self._finish(
                    consumer,
                    event_id,
                    InboxStatus.TERMINAL,
                    error_type=type(exc).__name__,
                )
                return True
            except Exception as exc:
                return await self._retry_or_dead_letter(
                    consumer, event_id, event_type, attempt, exc
                )

        await self._finish(consumer, event_id, InboxStatus.COMPLETED)
        return True

    async def _claim(
        self, consumer: str, event_id: UUID, event_type: str
    ) -> int | None:
        now = datetime.now(timezone.utc)
        async with self._session_maker() as session, session.begin():
            await session.execute(
                insert(DomainEventInbox)
                .values(
                    consumer=consumer,
                    event_id=event_id,
                    event_type=event_type,
                    status=InboxStatus.PROCESSING.value,
                    attempts=0,
                    delivery_count=0,
                    first_received_at=now,
                    last_received_at=now,
                )
                .on_conflict_do_nothing(index_elements=["consumer", "event_id"])
            )
            row = await session.scalar(
                select(DomainEventInbox)
                .where(
                    DomainEventInbox.consumer == consumer,
                    DomainEventInbox.event_id == event_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.status in {
                InboxStatus.COMPLETED.value,
                InboxStatus.TERMINAL.value,
                InboxStatus.DEAD_LETTER.value,
            }:
                return None
            if (
                row.status == InboxStatus.PROCESSING.value
                and row.delivery_count > 0
                and row.last_received_at > now - self.abandon_after
            ):
                return None
            row.status = InboxStatus.PROCESSING.value
            row.delivery_count += 1
            row.last_received_at = now
            row.last_error_type = None
            row.last_error = None
            return row.attempts + 1

    async def _retry_or_dead_letter(
        self,
        consumer: str,
        event_id: UUID,
        event_type: str,
        attempt: int,
        exc: Exception,
    ) -> bool:
        terminal = attempt >= self.max_attempts
        status = InboxStatus.DEAD_LETTER if terminal else InboxStatus.RETRYING
        await self._finish(
            consumer,
            event_id,
            status,
            error_type=type(exc).__name__,
            failed_attempts=attempt,
        )
        retry_counter.add(1, {"consumer": consumer, "event_type": event_type})
        if terminal:
            dead_letter_counter.add(1, {"consumer": consumer, "event_type": event_type})
            logger.error(
                "Event delivery dead-lettered",
                consumer=consumer,
                event_id=str(event_id),
                event_type=event_type,
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            return True
        raise exc

    async def _finish(
        self,
        consumer: str,
        event_id: UUID,
        status: InboxStatus,
        *,
        error_type: str | None = None,
        failed_attempts: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_maker() as session, session.begin():
            row = await session.scalar(
                select(DomainEventInbox)
                .where(
                    DomainEventInbox.consumer == consumer,
                    DomainEventInbox.event_id == event_id,
                )
                .with_for_update()
            )
            if row is None:
                return
            row.status = status.value
            if failed_attempts is not None:
                row.attempts = failed_attempts
            row.last_received_at = now
            row.completed_at = (
                now if status in {InboxStatus.COMPLETED, InboxStatus.TERMINAL} else None
            )
            row.dead_lettered_at = now if status == InboxStatus.DEAD_LETTER else None
            row.last_error_type = error_type[:200] if error_type else None
            row.last_error = (
                "Event handling failed; inspect the correlated trace"
                if error_type
                else None
            )


domain_event_inbox = InboxConsumer(async_session_maker)


def provide_domain_event_inbox() -> EventInboxPort:
    return domain_event_inbox
