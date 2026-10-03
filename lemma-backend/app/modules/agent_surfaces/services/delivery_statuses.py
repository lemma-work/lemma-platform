"""Acting on a platform's report that a message it accepted never arrived.

WhatsApp answers a send with a message id and reports failure later, by
webhook. Before this, those webhooks parsed as "not a message" and were
dropped: the agent believed it had been heard, a notification stayed DELIVERED,
and the person never knew anything had been said.

Now the failure is traced through the outbound log to what was sent, and:

* a **notification** is delivered again on the agent's other channels, never
  the one that just failed;
* a **reply** the person cannot receive on the chat -- their reply window had
  closed, or the number cannot receive messages -- goes to their email, the same
  fallback ``reply_window_fallback`` makes before a send;
* either way, the conversation's agent is told, so it does not go on assuming
  a message it sent was read.

Each failure is acted on once: the row is flipped to FAILED first, and a status
that arrives again finds nothing to flip.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.delivery_result import SurfaceDeliveryResult
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
    platform_value_for_source,
)
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.domain.outbound_messages import (
    FailedDeliveryStatus,
    SurfaceOutboundMessage,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.outbound_models import (
    OUTBOUND_KIND_NOTIFICATION,
    OUTBOUND_KIND_REPLY,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.outbound_message_repository import (  # noqa: E501
    SurfaceOutboundMessageRepository,
)
from app.modules.agent_surfaces.platforms.whatsapp.statuses import (
    UNREACHABLE_ON_WHATSAPP,
)
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery
from app.modules.agent_surfaces.services.notification_delivery import channel_label
from app.modules.agent_surfaces.services.notification_service import (
    NotificationService,
)
from app.modules.agent_surfaces.services.reply_window_fallback import (
    deliver_after_the_window,
)

logger = get_logger(__name__)


async def apply_delivery_statuses(
    uow_factory: UnitOfWorkFactory,
    payload: dict[str, object],
    *,
    source: str,
    adapters: SurfacePlatformAdapterRegistry | None = None,
) -> int:
    """Act on every failure this webhook reports. Returns how many were new."""
    return await DeliveryStatusHandler(uow_factory, adapters=adapters).apply(
        payload, source=source
    )


def _notification_service(uow: SqlAlchemyUnitOfWork) -> NotificationService:
    from app.modules.agent_surfaces.composition import build_notification_service

    return build_notification_service(uow)


def _surface_delivery(uow: SqlAlchemyUnitOfWork) -> SurfaceDelivery:
    from app.modules.agent_surfaces.composition import build_surface_delivery

    return build_surface_delivery(uow)


class DeliveryStatusHandler:
    """Traces each reported failure to what was sent, and does the one right thing.

    Every collaborator is a constructor argument with the real one as default,
    so a test hands its own instead of patching this module.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        adapters: SurfacePlatformAdapterRegistry | None = None,
        outbound: Callable[..., SurfaceOutboundMessageRepository] = (
            SurfaceOutboundMessageRepository
        ),
        notifications: Callable[..., NotificationRepository] = NotificationRepository,
        notification_service: Callable[..., NotificationService] = (
            _notification_service
        ),
        surface_delivery: Callable[..., SurfaceDelivery] = _surface_delivery,
        after_the_window: Callable[..., Awaitable[SurfaceDeliveryResult]] = (
            deliver_after_the_window
        ),
        append_notice: Callable[..., Awaitable[None]] = (
            agent_conversations.append_delivery_notice
        ),
    ) -> None:
        self.uow_factory = uow_factory
        self.adapters = adapters or SurfacePlatformAdapterRegistry()
        self.outbound = outbound
        self.notifications = notifications
        self.notification_service = notification_service
        self.surface_delivery = surface_delivery
        self.after_the_window = after_the_window
        self.append_notice = append_notice

    async def apply(self, payload: dict[str, object], *, source: str) -> int:
        platform = platform_value_for_source(source)
        adapter = self.adapters.get(platform or "")
        if adapter is None or platform is None:
            return 0
        acted = 0
        for failure in adapter.parse_delivery_statuses(payload):
            if await self._apply_one(failure, platform=platform):
                acted += 1
        return acted

    async def _apply_one(self, failure: FailedDeliveryStatus, *, platform: str) -> bool:
        async with self.uow_factory() as uow:
            repository = self.outbound(uow.session)
            sent = await repository.get_by_external_id(
                platform=platform, external_message_id=failure.external_message_id
            )
            if sent is None:
                # Not ours on record: sent before the log existed, past
                # retention, or not by a conversation (a verification code).
                logger.info(
                    "agent_surfaces.delivery_statuses.unknown_message.observed",
                    platform=platform,
                    failure_code=failure.code,
                )
                return False
            # Flipped and committed before anything is done about it, so a
            # status delivered twice in parallel is acted on by one of them.
            if not await repository.mark_failed(sent.id, error=_error_text(failure)):
                return False
            await uow.commit()
        logger.warning(
            "agent_surfaces.delivery_statuses.send_failed.degraded",
            platform=platform,
            kind=sent.kind,
            failure_code=failure.code,
            failure_title=failure.title,
            conversation_id=str(sent.conversation_id) if sent.conversation_id else None,
        )
        if sent.kind == OUTBOUND_KIND_NOTIFICATION and sent.notification_id:
            await self._redeliver_notification(sent, platform=platform)
        elif sent.kind == OUTBOUND_KIND_REPLY and sent.conversation_id:
            await self._recover_reply(sent, failure, platform=platform)
        return True

    async def _redeliver_notification(
        self, sent: SurfaceOutboundMessage, *, platform: str
    ) -> None:
        """The same notification, on any of the agent's channels but this one."""
        assert sent.notification_id is not None  # guarded by the caller
        async with self.uow_factory() as uow:
            notification = await self.notifications(uow).get(sent.notification_id)
            if notification is None:
                return
            redelivered = await self.notification_service(uow).deliver(
                notification, exclude_platform=SurfacePlatform(platform)
            )
        logger.info(
            "agent_surfaces.delivery_statuses.notification_redelivered.observed",
            notification_id=str(sent.notification_id),
            delivery_status=str(redelivered.delivery_status),
        )

    async def _recover_reply(
        self,
        sent: SurfaceOutboundMessage,
        failure: FailedDeliveryStatus,
        *,
        platform: str,
    ) -> None:
        """Email it where the chat cannot carry it, and tell the agent either way."""
        assert sent.conversation_id is not None  # guarded by the caller
        outcome = SurfaceDeliveryResult.undelivered()
        async with self.uow_factory() as uow:
            if failure.code in UNREACHABLE_ON_WHATSAPP and sent.body:
                delivery = self.surface_delivery(uow)
                target = await delivery.resolve_egress_target(sent.conversation_id)
                if target is not None:
                    outcome = await self.after_the_window(
                        uow,
                        target,
                        envelope=SurfaceEnvelope(text=sent.body),
                        metadata=await delivery.egress_metadata(target),
                        conversation_id=sent.conversation_id,
                    )
            await self.append_notice(
                uow,
                conversation_id=sent.conversation_id,
                notice=_notice(platform, failure, outcome),
                metadata={"external_message_id": sent.external_message_id},
            )


def _error_text(failure: FailedDeliveryStatus) -> str:
    return " ".join(part for part in (failure.code, failure.title) if part) or "failed"


def _notice(
    platform: str, failure: FailedDeliveryStatus, outcome: SurfaceDeliveryResult
) -> str:
    """What the agent is told, in words it can repeat to the person."""
    where = channel_label(platform)
    why = f" ({failure.title})" if failure.title else ""
    if outcome.by_email:
        return (
            f"Delivery notice: your previous message did not reach them on "
            f"{where}{why}. It was sent to their email address instead."
        )
    return (
        f"Delivery notice: your previous message did not reach them on "
        f"{where}{why}. They have not seen it."
    )


__all__ = ["DeliveryStatusHandler", "apply_delivery_statuses"]
