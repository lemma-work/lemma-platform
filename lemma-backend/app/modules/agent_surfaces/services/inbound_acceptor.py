"""Accepting one inbound delivery: classify, answer what has a deadline, store the rest.

The one path every way in takes -- the shared platform webhook, a pooled
number's or a surface's own webhook, the Resend webhook, and every native
receiver (Telegram polling, Slack Socket Mode, Resend polling):

1. **Classify** from the body (``inbound_classifier``). What nobody acts on --
   echoes, edits, reactions, receipts, chatter in a channel no group adopted --
   ends here, answered ``200``, with no row and no item.
2. **Provider deadlines, synchronously**: a Telegram group's ``/start <code>``
   (spend the code, say hello), Slack's in-chat setup modal (its
   ``trigger_id`` dies in ~3 s), and a tapped button or a submitted form (the
   decision is recorded, and the approval's execution item written, in this
   request; the platform is acknowledged in it). No worker is involved, so a
   worker that is down or busy cannot make Slack say "this app did not respond".
3. **Platform updates** (the bot joined a channel, the app home opened, a
   WhatsApp group was created, a send failed) become a ``surface.platform_update``
   item.
4. **Messages**: one ``surface_inbound_messages`` row per message the delivery
   carries (``ON CONFLICT DO NOTHING`` on ``(receiver_scope, source_event_id)``)
   plus its ``surface.ingest`` item, in one transaction. The unique conflict is
   the dedup, kept for the row's retention (7 days once ROUTED or DROPPED, 30
   once FAILED): a redelivery inside that is a no-op.

A receiver passes ``after_store`` to advance its cursor in the same transaction
as the rows it read.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import UUID

from app.core.observability.product_metrics import record_surface_message

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkWriter, pod_namespace
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
    platform_value_for_source,
)
from app.modules.agent_surfaces.domain.inbound_messages import part_source_event_id
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.work_payloads import (
    IngestPayload,
    PlatformUpdatePayload,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_INGEST,
    SURFACE_PLATFORM_UPDATE,
    ingest_key,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
    InboundMessageRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.inbound_classifier import (
    Verdict,
    classify,
    thread_key,
)

logger = get_logger(__name__)

type InboundRequest = SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress
#: Runs in the transaction that stores the rows, after them (a receiver cursor).
type AfterStore = Callable[[SqlAlchemyUnitOfWork], Awaitable[None]]

Outcome = Literal["dropped", "handled", "stored", "duplicate"]


@dataclass(frozen=True, slots=True)
class InboundDelivery:
    """One delivery as it arrived, with the identity that dedups it."""

    request: InboundRequest
    #: The receiver it arrived on: ``shared``, a surface id, a pooled number, a
    #: receiver key. A provider's id is unique only within one receiver.
    receiver_scope: str
    source_event_id: str
    #: The platform, when the route knows it better than the request does (a
    #: surface's own webhook names the surface, not the platform).
    platform: SurfacePlatform | None = None
    #: The pod the receiving surface belongs to, when the route already knows.
    pod_id: UUID | None = None


@dataclass(slots=True)
class AcceptResult:
    outcome: Outcome
    verdict: Verdict
    row_ids: list[UUID] = field(default_factory=list)


def _platform(delivery: InboundDelivery) -> SurfacePlatform | None:
    if delivery.platform is not None:
        return delivery.platform
    request = delivery.request
    if isinstance(request, SurfacePlatformWebhookIngress):
        return SurfacePlatform.from_source(request.source)
    return None


def _source(delivery: InboundDelivery, platform: SurfacePlatform | None) -> str:
    request = delivery.request
    if isinstance(request, SurfacePlatformWebhookIngress):
        return request.source
    return platform.value.lower() if platform else "unknown"


class InboundAcceptor:
    """Steps 1-4 above for one delivery. Holds no session across a platform call."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        adapters: SurfacePlatformAdapterRegistry | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.adapters = adapters or SurfacePlatformAdapterRegistry()

    async def accept(
        self, delivery: InboundDelivery, *, after_store: AfterStore | None = None
    ) -> AcceptResult:
        platform = _platform(delivery)
        verdict = classify(platform, delivery.request.payload)
        if verdict.unless_adopted and platform is not None:
            verdict = await self._unless_adopted(platform, verdict)
        logger.info(
            "agent_surfaces.inbound.classified",
            platform=platform.value if platform else None,
            verdict=verdict.verdict,
            reason=verdict.drop_reason,
        )
        if verdict.dropped:
            if after_store is not None:
                async with self.uow_factory() as uow:
                    await after_store(uow)
            return AcceptResult("dropped", verdict)
        surface = platform.value if platform else None
        try:
            result = await self._accept_kept(
                delivery, platform, verdict, after_store=after_store
            )
        except Exception:
            if verdict.message:
                record_surface_message(surface, direction="in", succeeded=False)
            raise
        if verdict.message:
            record_surface_message(surface, direction="in", succeeded=True)
        return result

    async def _accept_kept(
        self,
        delivery: InboundDelivery,
        platform: SurfacePlatform | None,
        verdict: Verdict,
        *,
        after_store: AfterStore | None,
    ) -> AcceptResult:
        if await self._handled_synchronously(delivery, platform, verdict):
            if after_store is not None:
                async with self.uow_factory() as uow:
                    await after_store(uow)
            return AcceptResult("handled", verdict)
        return await self._store(delivery, platform, verdict, after_store=after_store)

    async def _unless_adopted(
        self, platform: SurfacePlatform, verdict: Verdict
    ) -> Verdict:
        channel = verdict.unless_adopted
        assert channel is not None
        async with self.uow_factory() as uow:
            adopted = await SurfaceGroupRepository(uow.session).surface_ids_for_channel(
                platform=platform.value, external_channel_id=channel
            )
        if adopted:
            return replace(verdict, unless_adopted=None)
        return Verdict(drop_reason="unrouted_channel")

    async def _handled_synchronously(
        self,
        delivery: InboundDelivery,
        platform: SurfacePlatform | None,
        verdict: Verdict,
    ) -> bool:
        """The work a provider deadline will not let wait for a worker."""
        from app.modules.agent_surfaces.composition import (
            build_app_event_handler,
            build_surface_ingress,
        )
        from app.modules.agent_surfaces.services.onboarding_inputs import (
            is_onboarding_input,
        )
        from app.modules.agent_surfaces.services.telegram_group_join import (
            claim_telegram_group_join,
        )
        from app.modules.agent_surfaces.services.telegram_identity_link_claim import (
            claim_telegram_identity_link,
        )

        request = delivery.request
        text = str((request.payload.get("message") or {}).get("text") or "")
        if (
            verdict.message
            and platform is SurfacePlatform.TELEGRAM
            and "/start" in text
            and (
                # A member proving their account, in a private chat: before
                # onboarding or routing can take it for a question.
                (
                    "lk_" in text
                    and await claim_telegram_identity_link(
                        self.uow_factory, request, adapters=self.adapters
                    )
                )
                or await claim_telegram_group_join(
                    self.uow_factory, request, adapters=self.adapters
                )
            )
        ):
            return True
        if not verdict.interaction:
            return False
        async with self.uow_factory() as uow:
            if await build_app_event_handler(uow).try_handle_channel_setup(request):
                return True
            if is_onboarding_input(request.payload):
                return False
            return await build_surface_ingress(uow).try_handle_interaction(request)

    def _parts(
        self, delivery: InboundDelivery, platform: SurfacePlatform | None
    ) -> list[InboundRequest]:
        request = delivery.request
        adapter = self.adapters.get(platform.value) if platform else None
        if adapter is None:
            return [request]
        payloads = adapter.split_inbound_payloads(request.payload)
        if len(payloads) <= 1:
            return [request]
        logger.info(
            "agent_surfaces.ingress_service.webhook_carried_several_messages.observed",
            source=platform.value if platform else None,
            message_count=len(payloads),
        )
        return [request.model_copy(update={"payload": payload}) for payload in payloads]

    async def _store(
        self,
        delivery: InboundDelivery,
        platform: SurfacePlatform | None,
        verdict: Verdict,
        *,
        after_store: AfterStore | None,
    ) -> AcceptResult:
        request = delivery.request
        surface_id = (
            request.surface_id
            if isinstance(request, SurfaceDirectWebhookIngress)
            else None
        )
        receiver_surface_ids = (
            request.receiver_surface_ids
            if isinstance(request, SurfacePlatformWebhookIngress)
            else None
        )
        row_ids: list[UUID] = []
        async with self.uow_factory() as uow:
            pod_id = delivery.pod_id or await _pod_of(
                uow, surface_id=surface_id, receiver_surface_ids=receiver_surface_ids
            )
            ns = pod_namespace(pod_id) if pod_id else SYSTEM_NAMESPACE
            work = WorkWriter.of(uow)
            if verdict.platform_update:
                await work.add(
                    SURFACE_PLATFORM_UPDATE,
                    ns,
                    PlatformUpdatePayload(request=request),
                    dedup_key=f"platform-update:{delivery.receiver_scope}:{delivery.source_event_id}",
                )
            if verdict.message:
                repository = InboundMessageRepository(uow.session)
                source = _source(delivery, platform)
                for index, part in enumerate(self._parts(delivery, platform)):
                    key = thread_key(platform, part.payload)
                    row_id = await repository.insert(
                        receiver_scope=delivery.receiver_scope,
                        source_event_id=part_source_event_id(
                            delivery.source_event_id, index
                        ),
                        platform=(platform.value if platform else source.upper()),
                        source=source,
                        payload=part.payload,
                        headers=dict(part.headers),
                        thread_key=key,
                        surface_id=surface_id,
                        pod_id=pod_id,
                        receiver_surface_ids=receiver_surface_ids,
                    )
                    if row_id is None:
                        continue
                    row_ids.append(row_id)
                    await work.add(
                        SURFACE_INGEST,
                        ns,
                        IngestPayload(inbound_message_id=row_id),
                        dedup_key=ingest_key(row_id),
                        concurrency_key=f"thread:{surface_id or delivery.receiver_scope}:{key}",
                    )
            if after_store is not None:
                await after_store(uow)
        if verdict.message and not row_ids:
            logger.info(
                "agent_surfaces.inbound.duplicate_delivery.observed",
                platform=platform.value if platform else None,
                receiver_scope=delivery.receiver_scope,
            )
            return AcceptResult("duplicate", verdict)
        return AcceptResult("stored", verdict, row_ids)


async def _pod_of(
    uow: SqlAlchemyUnitOfWork,
    *,
    surface_id: UUID | None,
    receiver_surface_ids: list[UUID] | None,
) -> UUID | None:
    """The one pod the receiving surface(s) belong to, if there is exactly one.

    What makes ingest fair per pod: its items run in the pod's namespace. A
    shared bot's delivery has no pod until it is routed, and runs in ``system``.
    """
    ids = [surface_id] if surface_id is not None else list(receiver_surface_ids or [])
    if not ids or len(ids) > 20:
        return None
    repository = SurfaceRepository(uow)
    pods = set()
    for candidate in ids:
        surface = await repository.get(candidate)
        if surface is not None:
            pods.add(surface.pod_id)
    return pods.pop() if len(pods) == 1 else None


def platform_request(
    *,
    source: str,
    payload: dict,
    headers: dict[str, str],
    receiver_surface_ids: list[UUID] | None = None,
) -> SurfacePlatformWebhookIngress:
    return SurfacePlatformWebhookIngress(
        source=source,
        payload=payload,
        headers=headers,
        receiver_surface_ids=receiver_surface_ids,
    )


def source_platform(source: str) -> SurfacePlatform | None:
    value = platform_value_for_source(source)
    return SurfacePlatform(value) if value else None


__all__ = [
    "AcceptResult",
    "InboundAcceptor",
    "InboundDelivery",
    "platform_request",
    "source_platform",
]
