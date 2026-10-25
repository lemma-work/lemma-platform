"""``surface.ingest``: one stored inbound message, routed into a conversation.

Interactive class, a JOB (platform calls, file ingest, transcription), one item
at a time per thread (``concurrency_key``), so two webhooks for one thread are
ingested in the order they arrived and the second joins or follows the run the
first started.

The row moves ``RECEIVED -> INGESTING`` in a checkpoint, then everything slow
runs with no session held, then the *fenced completion* writes the inbound
message, the run and its ``agent.turn`` item (``stage_agent_turn``), the held
WhatsApp replies as deliveries, and the row's ROUTED -- one commit, so a
worker that dies anywhere before it leaves the row to be ingested again and
one that dies after it leaves nothing to redo.

A retry re-enters preparation under the same message claim (owned by this
row) and reuses the attachments the first attempt already saved (the ingest
memo), so it neither drops the message as a duplicate nor saves its files
twice. Attempts exhausted: the row goes FAILED and the sender of a direct
message is asked, once, to send it again (a ``surface_deliveries`` row).

Also replays a ready onboarding's held message (``onboarding_pending_id``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import (
    DeadContext,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.deliveries import (
    DeliveryContent,
    InboundSenderTarget,
    ReplyContextTarget,
)
from app.modules.agent_surfaces.domain.delivery_limits import CONSUMER_ATTEMPTS
from app.modules.agent_surfaces.domain.inbound_messages import InboundStatus
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingIngressResult
from app.modules.agent_surfaces.domain.work_payloads import IngestPayload
from app.modules.agent_surfaces.events.work_kinds import SURFACE_INGEST
from app.modules.agent_surfaces.infrastructure.inbound_delivery_models import (
    SurfaceInboundMessageModel,
)
from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
    InboundMessageRepository,
)
from app.modules.agent_surfaces.services.message_claims import claims_owned_by
from app.modules.agent_surfaces.services.surface_deliveries import stage_delivery
from app.modules.agent_surfaces.services.turn_starter import PROCESSING_FAILED_REPLY
from app.modules.connectors.contracts.pod_connectors import (
    standing_resolutions_once,
)

logger = get_logger(__name__)

type OnboardingHandler = Callable[
    [SurfaceIngressRequest], Awaitable[OnboardingIngressResult]
]


def request_of(row: SurfaceInboundMessageModel) -> SurfaceIngressRequest:
    """The delivery as it arrived, from its stored row."""
    if row.surface_id is not None:
        return SurfaceDirectWebhookIngress(
            surface_id=row.surface_id,
            payload=dict(row.payload),
            headers=dict(row.headers or {}),
        )
    return SurfacePlatformWebhookIngress(
        source=row.source,
        payload=dict(row.payload),
        headers=dict(row.headers or {}),
        receiver_surface_ids=(
            [UUID(str(item)) for item in row.receiver_surface_ids]
            if row.receiver_surface_ids is not None
            else None
        ),
    )


async def context_for_delivery(
    part: SurfaceIngressRequest,
    *,
    onboarding_handler: OnboardingHandler,
    uow_factory: UnitOfWorkFactory,
) -> AgentSurfaceContext | None:
    """Onboarding's answer for one delivery, or ordinary ingestion's.

    The two refusals caught here mean "this message cannot go the onboarding
    way", and neither gets better on a retry: a personal route dies when the pod
    is deleted, the person is removed from it, or the app is uninstalled.
    Ordinary ingestion is the right next thing -- it routes by pod membership,
    and where it cannot it says so.
    """
    from app.modules.agent_surfaces.composition import build_surface_ingress
    from app.modules.agent_surfaces.services.onboarding_private_delivery import (
        PrivateDeliveryUnavailable,
    )
    from app.modules.agent_surfaces.services.personal_dm_routes import (
        PersonalRouteUnavailable,
    )

    try:
        onboarding = await onboarding_handler(part)
    except (PersonalRouteUnavailable, PrivateDeliveryUnavailable) as unavailable:
        logger.info(
            "agent_surfaces.events.handlers.onboarding_route_unavailable",
            reason=str(unavailable),
        )
    else:
        if onboarding.handled:
            return onboarding.context
    async with uow_factory() as uow:
        return await build_surface_ingress(uow).prepare_ingress(part)


class _Finish:
    """The ingest's one fenced completion, handed to the turn starter.

    The starter writes the message in it; a reply that is not a run (a
    stranger's sign-up link) is staged as a delivery in it; and whatever the
    starter did not use it for, the handler finishes with at the end.
    """

    def __init__(
        self,
        ctx: JobContext,
        *,
        row_id: UUID | None,
        pod_id: UUID | None,
        on_complete: Callable[[SqlAlchemyUnitOfWork], Awaitable[None]] | None = None,
    ) -> None:
        self.ctx = ctx
        self.row_id = row_id
        self.pod_id = pod_id
        self.on_complete = on_complete
        self.completed = False
        self.context: AgentSurfaceContext | None = None

    async def _mark(
        self, uow: SqlAlchemyUnitOfWork, status: InboundStatus, reason: str | None
    ) -> None:
        if self.on_complete is not None:
            await self.on_complete(uow)
        if self.row_id is None:
            return
        context = self.context
        await InboundMessageRepository(uow.session).mark(
            self.row_id,
            status,
            reason=reason,
            conversation_id=(
                context.conversation_id
                if isinstance(context, SurfaceChatContext)
                else None
            ),
            pod_id=context.pod_id if isinstance(context, SurfaceChatContext) else None,
        )

    @asynccontextmanager
    async def write_scope(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        if self.completed:
            raise RuntimeError("surface.ingest completes once")
        async with self.ctx.complete_work() as uow:
            yield uow
            await self._mark(uow, InboundStatus.ROUTED, None)
        self.completed = True

    async def reply(self, context: SurfaceReplyContext) -> bool:
        if self.completed:
            return False
        async with self.ctx.complete_work() as uow:
            await stage_delivery(
                uow,
                idempotency_key=(
                    f"reply:{self.row_id}"
                    if self.row_id is not None
                    else f"reply:{self.ctx.item.id}"
                ),
                target=ReplyContextTarget(context=context.model_dump(mode="json")),
                content=DeliveryContent(
                    text=context.reply_message, purpose=context.reply_kind
                ),
                destination_key=(
                    f"reply:{context.platform.value}:{context.surface_id or '-'}:"
                    f"{context.event.external_channel_id or context.event.sender_external_user_id or '-'}"
                ),
                pod_id=self.pod_id,
                surface_id=context.surface_id,
                inbound_message_id=self.row_id,
            )
            await self._mark(uow, InboundStatus.ROUTED, context.reply_kind)
        self.completed = True
        return True

    async def conclude(self, status: InboundStatus, reason: str) -> None:
        if self.completed:
            return
        async with self.ctx.complete_work() as uow:
            await self._mark(uow, status, reason)
        self.completed = True


def _uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def _start(
    finish: _Finish, context: AgentSurfaceContext, uow_factory: UnitOfWorkFactory
) -> None:
    from app.modules.agent_surfaces.composition import build_surface_ingress
    from app.modules.agent_surfaces.services.turn_starter import SurfaceTurnStarter

    async def reprepare(
        request: SurfacePlatformWebhookIngress,
    ) -> AgentSurfaceContext | None:
        async with uow_factory() as uow:
            return await build_surface_ingress(uow).prepare_ingress(request)

    finish.context = context
    starter = SurfaceTurnStarter(
        uow_factory=uow_factory,
        reprepare=reprepare,
        write_scope=finish.write_scope,
        reply_sink=finish.reply,
    )
    try:
        await starter.execute_chat(context)
    except Exception:
        if not finish.completed:
            raise
        # The message, its run and the row are committed; what failed is the
        # courtesy after them (retiring an old card, a "could not read that").
        logger.warning(
            "agent_surfaces.ingest.after_routing_failed.degraded",
            item_id=str(finish.ctx.item.id),
            exc_info=True,
        )


async def _ingest_row(
    ctx: JobContext, row_id: UUID, uow_factory: UnitOfWorkFactory
) -> None:
    from app.modules.agent_surfaces.composition import build_surface_ingress
    from app.modules.agent_surfaces.services.chat_onboarding import (
        ChatOnboardingCoordinator,
    )
    from app.modules.agent_surfaces.services.onboarding_inputs import (
        is_onboarding_input,
    )

    async with ctx.checkpoint() as uow:
        repository = InboundMessageRepository(uow.session)
        row = await repository.get(row_id)
        if row is None or InboundStatus(row.status).finished:
            return
        await repository.mark(row_id, InboundStatus.INGESTING)
        request = request_of(row)
        pod_id = row.pod_id
    finish = _Finish(ctx, row_id=row_id, pod_id=pod_id)
    with claims_owned_by(str(row_id)):
        # A tap that arrived through a receiver (Socket Mode, polling) never
        # met an HTTP request to be answered in; and a body the classifier read
        # as a message may still be one.
        if not is_onboarding_input(request.payload):
            async with uow_factory() as uow:
                handled = await build_surface_ingress(uow).try_handle_interaction(
                    request
                )
            if handled:
                await finish.conclude(InboundStatus.DROPPED, "interaction")
                return
        context = await context_for_delivery(
            request,
            onboarding_handler=ChatOnboardingCoordinator(uow_factory).handle,
            uow_factory=uow_factory,
        )
        if context is None:
            await finish.conclude(InboundStatus.DROPPED, "nothing to answer")
            return
        await _start(finish, context, uow_factory)
    await finish.conclude(InboundStatus.ROUTED, "handled without a new message")


async def _replay_onboarding(
    ctx: JobContext, pending_id: UUID, uow_factory: UnitOfWorkFactory
) -> None:
    from app.modules.agent_surfaces.services.onboarding_replay import (
        mark_onboarding_handed_off,
        prepare_onboarding_replay,
    )

    async with uow_factory() as uow:
        context = await prepare_onboarding_replay(uow, pending_id)
    if context is None:
        return

    async def handed_off(uow: SqlAlchemyUnitOfWork) -> None:
        await mark_onboarding_handed_off(uow, pending_id)

    finish = _Finish(
        ctx,
        row_id=None,
        pod_id=context.pod_id if isinstance(context, SurfaceChatContext) else None,
        on_complete=handed_off,
    )
    with claims_owned_by(f"onboarding:{pending_id}"):
        await _start(finish, context, uow_factory)
    await finish.conclude(InboundStatus.ROUTED, "onboarding handed off")


def make_ingest(uow_factory: UnitOfWorkFactory | None = None):
    async def ingest(ctx: JobContext, payload: IngestPayload) -> None:
        factory = uow_factory or _uow_factory()
        if payload.onboarding_pending_id is not None:
            await _replay_onboarding(ctx, payload.onboarding_pending_id, factory)
            return
        assert payload.inbound_message_id is not None
        # Verifying the sender, its credentials and the identity it binds to
        # each ask what the surface's connector runs as: once per message.
        with standing_resolutions_once():
            await _ingest_row(ctx, payload.inbound_message_id, factory)

    return ingest


async def mark_failed(ctx: DeadContext) -> None:
    """Attempts exhausted: FAILED, and the sender asked once to send it again.

    The reply is a delivery, not a send: this transaction may not reach the
    network. Its sender resolves the person from the stored message and holds
    the stranger-reply window, and leaves a group alone -- a reply there is
    read by everyone in it, about one person's message.
    """
    payload = IngestPayload.model_validate(ctx.item.payload)
    row_id = payload.inbound_message_id
    if row_id is None:
        logger.error(
            "agent_surfaces.ingest.onboarding_replay_dead.failed",
            pending_id=str(payload.onboarding_pending_id),
            reason=ctx.reason,
        )
        return
    repository = InboundMessageRepository(ctx.uow.session)
    row = await repository.get(row_id)
    if row is None:
        return
    await repository.mark(
        row_id,
        InboundStatus.FAILED,
        reason=f"{ctx.reason}: {ctx.last_error or ''}",
    )
    logger.error(
        "agent_surfaces.ingest.message_not_processed.failed",
        inbound_message_id=str(row_id),
        platform=row.platform,
        reason=ctx.reason,
    )
    await stage_delivery(
        ctx.uow,
        idempotency_key=f"resend:{row_id}",
        target=InboundSenderTarget(inbound_message_id=row_id),
        content=DeliveryContent(text=PROCESSING_FAILED_REPLY, purpose="resend"),
        destination_key=f"resend:{row.receiver_scope}:{row.thread_key}",
        pod_id=row.pod_id,
        surface_id=row.surface_id,
        inbound_message_id=row_id,
        work=ctx.work,
    )


def register_surface_ingest_work(
    registry: WorkRegistry,
    *,
    uow_factory: UnitOfWorkFactory | None = None,
    retry: RetryPolicy | None = None,
) -> None:
    registry.handler(
        kind=SURFACE_INGEST,
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=IngestPayload,
        concurrency=1,
        retry=retry or RetryPolicy(max_attempts=CONSUMER_ATTEMPTS, max_lease_losses=3),
        on_dead=mark_failed,
    )(make_ingest(uow_factory))


register_surface_ingest_work(default_registry)
