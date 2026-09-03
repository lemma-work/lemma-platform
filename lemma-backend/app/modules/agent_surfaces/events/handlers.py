from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_task,
    streaq_worker,
)
from app.composition.surface_agent import get_conversation_service
from app.modules.agent_surfaces.api.dependencies import (
    get_surface_service,
    surface_repository_factory,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ingress_context import AgentSurfaceContext
from app.modules.agent_surfaces.domain.job_payloads import (
    SurfaceProcessMessageTaskPayload,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.surface_inbound import (
    release_ingress_claim,
)
from app.composition.surface_connectors import get_connector_service
from app.modules.pod.domain.events import PodDeletedEvent, PodEvents
from app.modules.identity.domain.events import IdentityEvents, UserMobileChangedEvent
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def provide_job_queue() -> SharedStreaqJobQueue:
    return get_streaq_job_queue()


def build_surface_event_handler(uow):
    return AgentSurfaceIngressService(
        uow=uow,
        surface_repository=surface_repository_factory(uow),
        conversation_link_repository=SurfaceConversationLinkRepository(uow),
        conversation_service=get_conversation_service(uow),
        connector_service=get_connector_service(uow),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
    )


@reliable_redis_stream_subscriber(
    router,
    "surface_events",
    group="surface-webhook-events",
    consumer="surface-webhook-events-consumer",
)
async def handle_surface_webhook(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    # ``surface_events`` also carries ``surface.connected`` and
    # ``surface.message.answered``, which exist for the analytics projections.
    # Only the webhook belongs here, so the parameter stays untyped and the
    # event is parsed after the tag check -- declaring
    # ``SurfaceWebhookReceivedEvent`` here instead moves validation ahead of the
    # acknowledgement, which turns every other event on the stream into a poison
    # message: never acked, and reclaimed by XAUTOCLAIM forever. That is not
    # hypothetical; it ran at ~119 redeliveries an hour until this was fixed,
    # and it grew by one permanently-stuck message per agent created, because
    # every agent is given an auto-provisioned Resend mailbox whose creation
    # publishes ``surface.connected``. ``handle_surface_schedule_event`` below
    # carries the same warning for ``schedule_events``.
    if event.get("event_type") != SurfaceWebhookReceivedEvent.get_event_type():
        return

    received = SurfaceWebhookReceivedEvent.model_validate(event)

    async def process() -> None:
        await _process_surface_webhook(
            received, fs_logger, uow_factory=uow_factory, job_queue=job_queue
        )

    await inbox.process("agent-surfaces.webhook", received, process)


async def _release_claim_for_retry(
    context: AgentSurfaceContext,
    *,
    event: SurfaceWebhookReceivedEvent,
) -> None:
    """Say the delivery reached no job, then hand its claim back.

    Said first, because it is true whether or not the release then works, and
    because ``error`` is the point: a message somebody sent has not been
    answered. The only record before was the duplicate line inside preparation,
    at ``debug``, which ``LOG_LEVEL=INFO`` drops -- so a message lost this way
    left no trace anywhere.

    Called from a ``finally``, so the exception being propagated is still the
    current one and ``exc_info`` carries the reason the enqueue failed.
    """
    surface_id = context.surface_id
    logger.error(
        "agent_surfaces.handlers.surface_message_not_enqueued.failed",
        source=event.source,
        surface_id=str(surface_id) if surface_id else None,
        # LOG014 reads "not inside an `except`" as "no exception to attach".
        # This runs while one is unwinding through a `finally`, where
        # `sys.exc_info()` is still the failure being propagated -- and that
        # traceback is the whole reason an operator can act on this line.
        exc_info=True,  # noqa: LOG014
    )
    await release_ingress_claim(
        context, event_dedup_store=get_surface_event_dedup_store()
    )


async def _process_surface_webhook(
    event: SurfaceWebhookReceivedEvent,
    fs_logger: Logger,
    *,
    uow_factory: UnitOfWorkFactory,
    job_queue: SharedStreaqJobQueue,
) -> None:

    if event.surface_id:
        ingress_request = SurfaceDirectWebhookIngress(
            surface_id=event.surface_id,
            payload=event.payload,
            headers=event.headers or {},
        )
    else:
        ingress_request = SurfacePlatformWebhookIngress(
            source=event.source,
            payload=event.payload,
            headers=event.headers or {},
            receiver_surface_ids=event.receiver_surface_ids,
        )

    async with uow_factory() as uow:
        handler = build_surface_event_handler(uow)
        # Lifecycle events (the bot joined a channel, someone opened the app
        # home) are about the app itself: they never become a conversation, so
        # they are answered and stopped before the interaction/message paths.
        # Channel setup is time-critical: Slack expires the modal trigger in
        # ~3 seconds, so it runs before anything slower.
        if await handler.try_handle_channel_setup(ingress_request):
            return

        if await handler.try_handle_lifecycle(ingress_request):
            return

        if await handler.try_handle_interaction(ingress_request):
            return

        # One delivery can carry more than one message on a platform that
        # batches; every other platform hands back the request unchanged.
        contexts = [
            (index, await handler.prepare_ingress(part))
            for index, part in enumerate(
                handler.split_webhook_deliveries(ingress_request)
            )
        ]

    for index, context in contexts:
        if not context:
            continue
        # `prepare_ingress` spent the delivery claim above, and the work that
        # claim guards is this enqueue. Losing the enqueue -- a Redis blip, a
        # worker restart, a cancellation -- while still holding the claim makes
        # the inbox's retry a no-op: the replay re-enters preparation, is told
        # the message is a duplicate, and drops it for good. `finally` rather
        # than `except` so cancellation counts too, since `CancelledError` is
        # not an `Exception`. The deterministic `_job_id` already makes a double
        # enqueue harmless, so handing the claim back costs nothing.
        enqueued = False
        try:
            await job_queue.enqueue(
                "process_surface_message",
                payload=SurfaceProcessMessageTaskPayload(context=context).model_dump(
                    mode="json"
                ),
                # The first part keeps the bare id, so the dedup key for an
                # ordinary single-message delivery is byte-identical to what it
                # was.
                _job_id=(
                    f"surface-event:{event.event_id}"
                    if index == 0
                    else f"surface-event:{event.event_id}:{index}"
                ),
            )
            enqueued = True
        finally:
            if not enqueued:
                await _release_claim_for_retry(context, event=event)


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="surface-pod-deletion-events",
    consumer="surface-pod-deletion-events-consumer",
)
async def on_pod_deleted(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    """Remove all surfaces for a deleted pod so its accounts become free."""
    if event.get("event_type") != PodDeletedEvent.get_event_type():
        return

    async def process() -> None:
        parsed = PodDeletedEvent.model_validate(event)
        async with uow_factory() as uow:
            await get_surface_service(uow).delete_all_surfaces_for_pod(parsed.pod_id)

    await inbox.process("agent-surfaces.pod-deletion", event, process)


@reliable_redis_stream_subscriber(
    router,
    IdentityEvents.STREAM,
    group="surface-identity-events",
    consumer="surface-identity-events-consumer",
)
async def on_identity_event(
    event: dict,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != UserMobileChangedEvent.get_event_type():
        return

    async def process() -> None:
        parsed = UserMobileChangedEvent.model_validate(event)
        async with uow_factory() as uow:
            await ExternalSurfaceUserRepository(uow).clear_resolved_user(parsed.user_id)

    await inbox.process("agent-surfaces.identity", event, process)


@streaq_task(name="process_surface_message")
async def process_surface_message(
    payload: dict,
):
    worker_ctx: AppWorkerContext = streaq_worker.context
    task_payload = SurfaceProcessMessageTaskPayload.model_validate(payload)
    # The service scopes its own short UoWs (credential read + message-write
    # tail) around the long external I/O inside execute_chat — platform API
    # calls, file ingestion, and voice transcription — so no pooled DB
    # connection is held during that I/O.
    service = worker_ctx.build_surface_event_handler_with_factory()
    await service.execute_chat(task_payload.context)
