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
from app.core.log.log import get_logger
from app.composition.surface_agent import get_conversation_service
from app.modules.agent_surfaces.api.dependencies import (
    get_surface_service,
    surface_repository_factory,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
    SurfaceScheduleIngress,
)
from app.modules.agent_surfaces.domain.job_payloads import (
    SurfaceProcessMessageTaskPayload,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.composition.surface_connectors import get_connector_service
from app.modules.pod.domain.events import PodDeletedEvent, PodEvents
from app.modules.schedule.domain.events.schedule import ScheduleEvents, ScheduleFired

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
    event: SurfaceWebhookReceivedEvent,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    async def process() -> None:
        await _process_surface_webhook(
            event, fs_logger, uow_factory=uow_factory, job_queue=job_queue
        )

    await inbox.process("agent-surfaces.webhook", event, process)


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
        if await handler.try_handle_interaction(ingress_request):
            return

        context = await handler.prepare_ingress(ingress_request)

    if not context:
        return

    await job_queue.enqueue(
        "process_surface_message",
        payload=SurfaceProcessMessageTaskPayload(context=context).model_dump(
            mode="json"
        ),
        _job_id=f"surface-event:{event.event_id}",
    )


@reliable_redis_stream_subscriber(
    router,
    ScheduleEvents.STREAM,
    group="surface-schedule-events",
    consumer="surface-schedule-events-consumer",
)
async def handle_surface_schedule_event(
    event: ScheduleFired,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    async def process() -> None:
        await _process_surface_schedule_event(
            event, fs_logger, uow_factory=uow_factory, job_queue=job_queue
        )

    await inbox.process("agent-surfaces.schedule", event, process)


async def _process_surface_schedule_event(
    event: ScheduleFired,
    fs_logger: Logger,
    *,
    uow_factory: UnitOfWorkFactory,
    job_queue: SharedStreaqJobQueue,
) -> None:
    async with uow_factory() as uow:
        handler = build_surface_event_handler(uow)
        context = await handler.prepare_ingress(
            SurfaceScheduleIngress(
                schedule_id=event.schedule_id,
                payload=event.payload,
                account_id=event.account_id,
                pod_id=event.pod_id,
                user_id=event.user_id,
            )
        )

    if not context:
        return

    await job_queue.enqueue(
        "process_surface_message",
        payload=SurfaceProcessMessageTaskPayload(context=context).model_dump(
            mode="json"
        ),
        _job_id=f"surface-schedule-event:{event.event_id}",
    )


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
            await get_surface_service(uow).delete_all_surfaces_for_pod(
                parsed.pod_id
            )

    await inbox.process("agent-surfaces.pod-deletion", event, process)


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
