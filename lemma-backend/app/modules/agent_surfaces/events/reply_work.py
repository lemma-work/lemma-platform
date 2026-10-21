"""``surface.reply``: the destination pump that sends ``surface_deliveries`` rows.

One item per destination (a Slack channel, a Telegram chat, a WhatsApp sender),
concurrency 1 on that key. Each run sends the destination's rows oldest first
until none is due:

* a row still SENDING past its deadline was being sent by somebody who died --
  this pump on an earlier attempt, or a run's observer -- and nobody can say
  whether it arrived. It is marked UNKNOWN and never sent again, and the
  conversation says so (``mark_outcome_unknown``), so a lost reply is visible;
* a row still SENDING within its deadline is somebody's send in flight (the
  observer closing a stream with the answer): the pump waits for it;
* a row not yet due (a ``Retry-After``, the observer's grace) holds everything
  behind it, so nothing overtakes it;
* otherwise the row is claimed SENDING in a committed checkpoint *before* the
  platform call, and settled after it. The call is bounded (``SEND_TIMEOUT``)
  below the claim's deadline, so a slow platform is a retry, not an UNKNOWN.

A transient refusal reschedules the row behind its ``Retry-After`` and defers
the pump to the same moment, charging nothing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from opentelemetry import metrics

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    Defer,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.deliveries import (
    SEND_TIMEOUT,
    SENDING_LEASE,
    DeliveryStatus,
)
from app.modules.agent_surfaces.events.work_kinds import SURFACE_REPLY
from app.modules.agent_surfaces.infrastructure.inbound_delivery_models import (
    SurfaceDeliveryModel,
)
from app.modules.agent_surfaces.infrastructure.repositories.delivery_repository import (
    DeliveryRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)
from app.modules.agent_surfaces.services.delivery_sender import (
    DeliverySender,
    PlatformDeliverySender,
)
from app.modules.agent_surfaces.services.surface_deliveries import (
    PumpPayload,
    SendResult,
    content_of,
    settle_claimed_send,
)

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
delivery_unknown_counter = meter.create_counter(
    "lemma.surface.delivery_unknown",
    description=(
        "Surface deliveries whose send was interrupted, so nobody can say "
        "whether the message arrived; never resent."
    ),
)

#: What the person reads in the conversation in Lemma about such a delivery.
UNKNOWN_DELIVERY_NOTICE = (
    "This reply may not have reached {surface}: sending it was interrupted, "
    "and it was not sent again in case it had arrived."
)

#: Rows one pump run sends before it yields its slot to other destinations.
_ROWS_PER_RUN = 50


def _default_sender() -> DeliverySender:
    from app.modules.agent_surfaces.composition import build_surface_egress

    return PlatformDeliverySender(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        egress_factory=build_surface_egress,
    )


async def _bounded_send(
    send: DeliverySender, row: SurfaceDeliveryModel, timeout: timedelta
) -> SendResult:
    """``send(row)``, given up as a transient failure once ``timeout`` passes.

    Shorter than the row's SENDING deadline, so a platform that is merely slow
    gets the row back PENDING and retried, instead of the row outliving its
    deadline and going UNKNOWN for good.
    """
    try:
        async with asyncio.timeout(timeout.total_seconds()):
            return await send(row)
    except TimeoutError as exc:
        logger.warning(
            "agent_surfaces.reply_work.send_timed_out.degraded",
            delivery_id=str(row.id),
            destination=row.destination_key,
            timeout_seconds=timeout.total_seconds(),
            exc_info=True,
        )
        return SendResult(
            delivered=False,
            transient=True,
            error=f"{type(exc).__name__}: no answer within {timeout.total_seconds():g}s",
        )


async def mark_outcome_unknown(
    uow: SqlAlchemyUnitOfWork, row: SurfaceDeliveryModel
) -> None:
    """A send nobody finished: UNKNOWN, never sent again -- and said out loud.

    Saying it twice is worse than maybe not saying it, so the row is not
    resent; but a reply that silently never arrived is the failure nobody
    notices. So the conversation in Lemma gets a line saying this reply may
    not have reached its platform, the miss is counted for alerting, and it
    is logged -- all in the transaction that marks it.
    """
    from app.modules.agent.contracts import (
        conversations_for_surfaces as agent_conversations,
    )

    marked = await DeliveryRepository(uow.session).set_delivery_status(
        row.id,
        DeliveryStatus.UNKNOWN,
        error="the send was interrupted; it may or may not have arrived",
    )
    if not marked:
        return
    platform = await _platform_of(uow, row.surface_id)
    content = content_of(row.content)
    if row.conversation_id is not None:
        await agent_conversations.append_delivery_notice(
            uow,
            conversation_id=row.conversation_id,
            message=UNKNOWN_DELIVERY_NOTICE.format(surface=_platform_label(platform)),
            metadata={
                "source": "surface_delivery",
                "delivery_id": str(row.id),
                "delivery_status": DeliveryStatus.UNKNOWN.value,
            },
        )
    delivery_unknown_counter.add(
        1, {"platform": platform or "none", "purpose": content.purpose}
    )
    logger.warning(
        "agent_surfaces.reply_work.send_outcome_unknown.degraded",
        delivery_id=str(row.id),
        destination=row.destination_key,
        conversation_id=str(row.conversation_id) if row.conversation_id else None,
        platform=platform,
        purpose=content.purpose,
    )


async def _platform_of(
    uow: SqlAlchemyUnitOfWork, surface_id: UUID | None
) -> str | None:
    if surface_id is None:
        return None
    surface = await SurfaceRepository(uow).get(surface_id)
    return surface.surface_type.value if surface is not None else None


def _platform_label(platform: str | None) -> str:
    capabilities = get_platform_capabilities(platform)
    return capabilities.display_name if capabilities else "the chat it was sent to"


def make_pump(
    *,
    sender: DeliverySender | None = None,
    sending_lease: timedelta = SENDING_LEASE,
    send_timeout: timedelta = SEND_TIMEOUT,
):
    async def pump(ctx: JobContext, payload: PumpPayload) -> None:
        send = sender or _default_sender()
        for _ in range(_ROWS_PER_RUN):
            ctx.raise_if_stopping()
            async with ctx.checkpoint() as uow:
                repository = DeliveryRepository(uow.session)
                row = await repository.oldest_open(payload.destination_key)
                if row is None:
                    return
                now = datetime.now(UTC)
                if row.status == DeliveryStatus.SENDING.value:
                    if row.sending_until is not None and row.sending_until > now:
                        raise Defer(row.sending_until - now)
                    await mark_outcome_unknown(uow, row)
                    continue
                if row.not_before > now:
                    raise Defer(row.not_before - now)
                if not await repository.claim(row.id, lease=sending_lease):
                    continue
                await uow.session.refresh(row)
            result = await _bounded_send(send, row, send_timeout)
            async with ctx.checkpoint() as uow:
                delay = await settle_claimed_send(uow, row.id, result)
            if delay is not None:
                raise Defer(delay)
        # More than one run's worth: go round again, behind other destinations.
        raise Defer(timedelta(0))

    return pump


def register_surface_reply_work(
    registry: WorkRegistry,
    *,
    sender: DeliverySender | None = None,
    sending_lease: timedelta = SENDING_LEASE,
    send_timeout: timedelta = SEND_TIMEOUT,
) -> None:
    registry.handler(
        kind=SURFACE_REPLY,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=PumpPayload,
        concurrency=1,
        # A failure here is the database, not the platform: every platform
        # refusal is recorded on the row and deferred, never raised.
        retry=RetryPolicy(max_attempts=5, max_lease_losses=10),
    )(make_pump(sender=sender, sending_lease=sending_lease, send_timeout=send_timeout))


register_surface_reply_work(default_registry)
