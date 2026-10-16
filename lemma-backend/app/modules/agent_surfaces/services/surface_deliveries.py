"""Writing ``surface_deliveries`` rows, and the destination pump that sends them.

Every writer goes through :func:`stage_delivery`, which inserts the row and
makes sure a ``surface.reply`` item will look at its destination, in the
caller's transaction: a delivery and the work that sends it commit together or
not at all.

The pump is one item per destination (``dest:<key>``, concurrency 1 on the same
key), so replies to one Slack channel or Telegram group leave in the order they
were written, a ``Retry-After`` holds back everything behind it rather than
letting later messages overtake it, and a burst costs one 429 per window
instead of one per message.

Every outbound message the platform must receive once goes this way: a run's
answer or failure notice (``surface.reply_for_run``), a held WhatsApp reply, a
reply to a stranger, a "please resend". No network here: the FAST
``surface.reply_for_run`` stages through this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    WorkWriter,
    pod_namespace,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.deliveries import (
    MAX_SEND_ATTEMPTS,
    RETRY_DELAYS,
    ConversationTarget,
    DeliveryContent,
    DeliveryStatus,
    DeliveryTarget,
)
from app.modules.agent_surfaces.events.work_kinds import SURFACE_REPLY
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.delivery_repository import (
    DeliveryRepository,
    queued_pump_run_at,
)

logger = get_logger(__name__)


class PumpPayload(BaseModel):
    destination_key: str


def pump_key(destination_key: str) -> str:
    return f"dest:{destination_key}"


def run_reply_idempotency_key(run_id: UUID) -> str:
    return f"run-reply:{run_id}"


async def conversation_destination(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> tuple[str, UUID | None]:
    """Where a conversation's messages go, as a pump key, and its surface.

    The surface thread, not the conversation: a Telegram group holds one
    conversation per member, and every one of them shares the group's limit.
    """
    link = await SurfaceConversationLinkRepository(uow).get_by_conversation_id(
        conversation_id
    )
    if link is None:
        return f"conversation:{conversation_id}", None
    place = link.external_channel_id or link.external_thread_id
    return f"{link.surface_id}:{place}", link.surface_id


async def ensure_pump(
    work: WorkWriter,
    *,
    session: Any,
    ns: str,
    destination_key: str,
    run_at: datetime | None,
) -> None:
    """A ``surface.reply`` item that will look at ``destination_key`` by ``run_at``.

    Coalesced with a queued one; a queued one due later is pulled forward, never
    pushed back (it may owe an earlier row). A running pump does not coalesce,
    so the queued twin this writes picks up what the running one misses.
    """
    key = pump_key(destination_key)
    added = await work.add(
        SURFACE_REPLY,
        ns,
        PumpPayload(destination_key=destination_key),
        dedup_key=key,
        concurrency_key=key,
        run_at=run_at,
    )
    if added is not None or run_at is None:
        return
    queued_at = await queued_pump_run_at(session, key)
    if queued_at is not None and run_at < queued_at:
        await work.reschedule(SURFACE_REPLY, ns, key, run_at=run_at)


def namespace_for(pod_id: UUID | None) -> str:
    return pod_namespace(pod_id) if pod_id is not None else SYSTEM_NAMESPACE


async def stage_delivery(
    uow: SqlAlchemyUnitOfWork,
    *,
    idempotency_key: str,
    target: DeliveryTarget,
    content: DeliveryContent,
    destination_key: str,
    pod_id: UUID | None,
    surface_id: UUID | None = None,
    conversation_id: UUID | None = None,
    agent_run_id: UUID | None = None,
    notification_id: UUID | None = None,
    inbound_message_id: UUID | None = None,
    not_before: datetime | None = None,
    work: WorkWriter | None = None,
) -> UUID | None:
    """Write one delivery and the pump that sends it. ``None``: already written."""
    row_id = await DeliveryRepository(uow.session).insert(
        idempotency_key=idempotency_key,
        destination_key=destination_key,
        target=target.model_dump(mode="json"),
        content=content.model_dump(mode="json"),
        pod_id=pod_id,
        surface_id=surface_id,
        conversation_id=conversation_id,
        agent_run_id=agent_run_id,
        notification_id=notification_id,
        inbound_message_id=inbound_message_id,
        not_before=not_before,
    )
    if row_id is None:
        return None
    await ensure_pump(
        work or WorkWriter.of(uow),
        session=uow.session,
        ns=namespace_for(pod_id),
        destination_key=destination_key,
        run_at=not_before,
    )
    return row_id


async def stage_conversation_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    idempotency_key: str,
    conversation_id: UUID,
    pod_id: UUID | None,
    content: DeliveryContent,
    agent_run_id: UUID | None = None,
    not_before: datetime | None = None,
    work: WorkWriter | None = None,
) -> UUID | None:
    """A message to the surface thread ``conversation_id`` is bound to."""
    destination, surface_id = await conversation_destination(uow, conversation_id)
    return await stage_delivery(
        uow,
        idempotency_key=idempotency_key,
        target=ConversationTarget(conversation_id=conversation_id),
        content=content,
        destination_key=destination,
        pod_id=pod_id,
        surface_id=surface_id,
        conversation_id=conversation_id,
        agent_run_id=agent_run_id,
        not_before=not_before,
        work=work,
    )


def retry_delay(attempts: int, retry_after: float | None) -> timedelta | None:
    """The wait before the next try after ``attempts`` sends, or None: give up."""
    if attempts >= MAX_SEND_ATTEMPTS:
        return None
    step = RETRY_DELAYS[min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)]
    return max(step, timedelta(seconds=retry_after or 0.0))


class SendResult(BaseModel):
    delivered: bool
    transient: bool = False
    partial: bool = False
    retry_after: float | None = None
    error: str | None = None


async def settle_claimed_send(
    uow: SqlAlchemyUnitOfWork, row_id: UUID, result: SendResult
) -> timedelta | None:
    """Record how a claimed send went. Returns the wait before a retry, if one.

    A refusal the platform called transient, with nothing of the message
    delivered, goes back to PENDING behind its ``Retry-After``; a partial
    delivery or a permanent refusal is FAILED, since sending it again would
    repeat the part the person has or be refused again.
    """
    repository = DeliveryRepository(uow.session)
    if result.delivered:
        await repository.set_delivery_status(row_id, DeliveryStatus.SENT)
        return None
    row = await repository.get(row_id)
    if row is None:
        return None
    delay = (
        retry_delay(row.attempts, result.retry_after)
        if result.transient and not result.partial
        else None
    )
    if delay is None:
        await repository.set_delivery_status(
            row_id,
            DeliveryStatus.FAILED,
            error=result.error
            or ("partly delivered" if result.partial else "the platform refused it"),
        )
        logger.error(
            "agent_surfaces.surface_deliveries.delivery_failed.failed",
            delivery_id=str(row_id),
            attempts=row.attempts,
            partial=result.partial,
            transient=result.transient,
        )
        return None
    not_before = datetime.now(UTC) + delay
    await repository.retry_later(row_id, not_before=not_before, error=result.error)
    await ensure_pump(
        WorkWriter.of(uow),
        session=uow.session,
        ns=namespace_for(row.pod_id),
        destination_key=row.destination_key,
        run_at=not_before,
    )
    logger.info(
        "agent_surfaces.surface_deliveries.retry_scheduled.observed",
        delivery_id=str(row_id),
        attempts=row.attempts,
        delay_seconds=delay.total_seconds(),
    )
    return delay


def content_of(raw: dict[str, Any]) -> DeliveryContent:
    return DeliveryContent.model_validate(raw)


__all__ = [
    "PumpPayload",
    "SendResult",
    "content_of",
    "conversation_destination",
    "ensure_pump",
    "namespace_for",
    "pump_key",
    "retry_delay",
    "run_reply_idempotency_key",
    "settle_claimed_send",
    "stage_conversation_message",
    "stage_delivery",
]
