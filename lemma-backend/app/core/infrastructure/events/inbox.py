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

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract
from faststream.exceptions import NackMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.models import DomainEventInbox
from app.core.log.log import get_logger
from app.core.origin import origin_from_payload, origin_scope
from app.core.request_context import event_lineage


logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
retry_counter = meter.create_counter("lemma.event.inbox.retry")
dead_letter_counter = meter.create_counter("lemma.event.inbox.dead_lettered")


class ClaimOutcome(StrEnum):
    """Why a delivery is not going to run, when it is not going to run.

    These were one value (``None``), and collapsing them lost an event every
    time a worker died. ``None`` meant "do not run this", the handler returned
    normally, and FastStream's acknowledgement middleware read that clean return
    as success and XACKed -- which is the only thing that removes an entry from
    the stream's pending-entries list. For a genuinely finished event that is
    right. For one another worker was still holding it was fatal: if that worker
    then died, the row stayed PROCESSING forever and the message was already
    gone from the PEL, so nothing ever redelivered it.

    Observed in production on 2026-09-21 as ~28,500 pending entries against
    rows stuck in PROCESSING since July, while the triggers those events were
    supposed to fire never fired.
    """

    #: Finished, or given up on, by someone. Acknowledge: the work is done and
    #: redelivering it would only find the same answer.
    ALREADY_SETTLED = "ALREADY_SETTLED"
    #: Claimed by a live worker within ``abandon_after``. Must NOT be
    #: acknowledged -- the PEL entry is the only record that this event still
    #: needs doing if that worker does not finish.
    IN_FLIGHT_ELSEWHERE = "IN_FLIGHT_ELSEWHERE"


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
        abandon_after_seconds: float | None = None,
    ) -> None:
        self._session_maker = session_maker
        self.max_attempts = max_attempts
        # Taken from the reclaim subscriber's idle threshold rather than named
        # again here, because the two have to agree and nothing said so. A
        # delivery that is handed back (see `ClaimOutcome.IN_FLIGHT_ELSEWHERE`)
        # comes round again when the reclaimer's `min_idle_time` expires, and
        # the claim it collided with is exactly that old by then. Hold claims
        # for *longer* than that and the returning delivery is handed back
        # again, and again, for as long as the difference lasts.
        #
        # They were two independent numbers that happened to both be 60: one a
        # setting, one a literal in this signature. Deriving one from the other
        # makes the agreement a property of the code instead of a coincidence
        # that the next person to tune `REDIS_STREAM_MIN_IDLE_TIME_MS` would
        # have silently broken.
        if abandon_after_seconds is None:
            abandon_after_seconds = (
                event_transport_settings.redis_stream_min_idle_time_ms / 1000
            )
        self.abandon_after = timedelta(seconds=abandon_after_seconds)

    async def process(
        self,
        consumer: str,
        event: BaseModel | Mapping[str, Any],
        handler: Callable[[], Awaitable[None]],
    ) -> bool:
        payload = normalized_event_payload(event)
        carrier = {
            key: str(payload[key])
            for key in ("traceparent", "tracestate")
            if payload.get(key)
        }
        context_token = otel_context.attach(extract(carrier, context=Context()))
        try:
            event_id = stable_event_id(event)
            event_type = _event_type(event)
            claimed = await self._claim(consumer, event_id, event_type)
            if claimed is ClaimOutcome.IN_FLIGHT_ELSEWHERE:
                # Hand the delivery back instead of returning quietly. On a
                # Redis stream `nack` is precisely "do not XACK", so the entry
                # stays in the pending-entries list and the reclaim subscriber
                # offers it again once the holder's claim has aged out. The
                # alternative -- returning False, as this did -- acknowledges,
                # and an acknowledged entry a dead worker was holding is an
                # event nothing will ever run.
                logger.debug(
                    "infrastructure.inbox.delivery_held_for_reclaim.observed",
                    consumer=consumer,
                    event_id=str(event_id),
                    event_type=event_type,
                )
                raise NackMessage
            if not isinstance(claimed, int):
                return False
            attempt = claimed

            with tracer.start_as_current_span("lemma.inbox.consume") as span:
                span.set_attribute("lemma.event_id", str(event_id))
                span.set_attribute("lemma.event_type", event_type)
                span.set_attribute("lemma.consumer", consumer)
                span.set_attribute("lemma.attempt", attempt)
                raw_correlation_id = payload.get("correlation_id")
                try:
                    correlation_id = UUID(str(raw_correlation_id))
                except TypeError, ValueError:
                    correlation_id = event_id
                raw_causation_id = payload.get("causation_id")
                try:
                    causation_id = UUID(str(raw_causation_id))
                except TypeError, ValueError:
                    causation_id = None
                with (
                    event_lineage(
                        correlation_id=correlation_id,
                        event_id=event_id,
                        causation_id=causation_id,
                        request_id=(
                            str(payload["request_id"])
                            if payload.get("request_id")
                            else None
                        ),
                        event_type=event_type,
                        consumer=consumer,
                    ),
                    origin_scope(origin_from_payload(payload)),
                ):
                    # Origin rides on the event, so a handler that raises its own
                    # domain events inherits how the *original* work arrived --
                    # a pod created by an import stays IMPORT, a run started by a
                    # schedule stays SCHEDULE. Deriving it from this worker's own
                    # surroundings instead is how the dimension goes quietly
                    # wrong: the worker knows nothing about the caller.
                    try:
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
                            "infrastructure.inbox.terminal_event_validation.degraded",
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
        finally:
            otel_context.detach(context_token)

    async def _claim(
        self, consumer: str, event_id: UUID, event_type: str
    ) -> int | ClaimOutcome:
        """The attempt number to run as, or why this delivery must not run.

        Returns three distinguishable things where it used to return two,
        because the caller has to acknowledge two of them differently. See
        :class:`ClaimOutcome`.
        """
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
                # Not reachable through the insert above, which either wrote the
                # row or lost to a concurrent write of it. Settled rather than
                # in-flight on purpose: with no row there is nothing to wait for,
                # and holding the delivery would loop on an empty claim forever.
                return ClaimOutcome.ALREADY_SETTLED
            if row.status in {
                InboxStatus.COMPLETED.value,
                InboxStatus.TERMINAL.value,
                InboxStatus.DEAD_LETTER.value,
            }:
                return ClaimOutcome.ALREADY_SETTLED
            if (
                row.status == InboxStatus.PROCESSING.value
                and row.delivery_count > 0
                and row.last_received_at > now - self.abandon_after
            ):
                # Someone else is mid-flight. Returning *before* the refresh
                # below is load-bearing: this branch leaves `last_received_at`
                # alone, so the holder's claim keeps ageing and the next
                # redelivery (at least `abandon_after` later, since that is the
                # reclaim subscriber's idle threshold) falls through and
                # re-claims. That is what stops a held delivery bouncing
                # forever.
                return ClaimOutcome.IN_FLIGHT_ELSEWHERE
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
                "infrastructure.inbox.event_delivery_dead_lettered.failed",
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
