from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


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
    inbox_attempt,
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
from app.modules.agent_surfaces.composition import (
    build_app_event_handler,
    build_surface_ingress,
    build_surface_service,
    build_surface_turn_starter,
)
from app.modules.agent_surfaces.domain.events import (
    SurfaceWebhookReceivedEvent,
    SurfaceOnboardingReadyEvent,
    SurfaceEvents,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceIngressRequest,
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ingress_context import AgentSurfaceContext
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingIngressResult
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.domain.job_payloads import (
    SurfaceProcessMessageTaskPayload,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.delivery_statuses import (
    log_delivery_statuses,
)
from app.modules.agent_surfaces.services.group_updates import apply_group_updates

from app.modules.agent_surfaces.services.telegram_group_join import (
    claim_telegram_group_join,
)
from app.modules.agent_surfaces.services.surface_inbound import (
    release_ingress_claim,
)
from app.modules.pod.domain.events import PodDeletedEvent, PodEvents
from app.modules.identity.domain.events import IdentityEvents, UserMobileChangedEvent
from app.core.log.log import get_logger

from app.modules.agent_surfaces.domain.delivery_limits import CONSUMER_ATTEMPTS

logger = get_logger(__name__)

router = RedisRouter()

EXPIRED_FORM_TEXT = (
    "That form has expired. Send me any message and I'll send a fresh one."
)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def provide_job_queue() -> SharedStreaqJobQueue:
    return get_streaq_job_queue()


@reliable_redis_stream_subscriber(
    router,
    SurfaceEvents.STREAM,
    group="agent-surfaces.onboarding",
    consumer="agent-surfaces.onboarding-consumer",
)
async def handle_onboarding_ready(
    event: dict[str, object],
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != SurfaceOnboardingReadyEvent.get_event_type():
        return

    async def process() -> None:
        from app.modules.agent_surfaces.services.onboarding_replay import (
            replay_onboarding,
        )

        ready = SurfaceOnboardingReadyEvent.model_validate(event)
        await replay_onboarding(
            ready.pending_id, uow_factory=uow_factory, job_queue=job_queue
        )

    await inbox.process(
        "agent-surfaces.onboarding", event, process, max_attempts=CONSUMER_ATTEMPTS
    )


def provide_onboarding_handler(
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
) -> Callable[[SurfaceIngressRequest], Awaitable[OnboardingIngressResult]]:
    from app.modules.agent_surfaces.services.chat_onboarding import (
        ChatOnboardingCoordinator,
    )

    return ChatOnboardingCoordinator(uow_factory).handle


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
    onboarding_handler: Callable[
        [SurfaceIngressRequest], Awaitable[OnboardingIngressResult]
    ] = Depends(provide_onboarding_handler),
) -> None:
    # ``surface_events`` also carries ``surface.connected``, which exists for the
    # analytics projections.
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
            received,
            fs_logger,
            uow_factory=uow_factory,
            job_queue=job_queue,
            onboarding_handler=onboarding_handler,
        )

    await inbox.process(
        "agent-surfaces.webhook", received, process, max_attempts=CONSUMER_ATTEMPTS
    )


async def _context_for_delivery(
    part: SurfaceIngressRequest,
    *,
    onboarding_handler: Callable[
        [SurfaceIngressRequest], Awaitable[OnboardingIngressResult]
    ],
    uow_factory: UnitOfWorkFactory,
    source: str = "",
    event_dedup_store: SurfaceEventDedupStorePort | None = None,
    tell: Callable[..., Awaitable[bool]] | None = None,
) -> AgentSurfaceContext | None:
    """Onboarding's answer for one delivery, or ordinary ingestion's.

    The two refusals caught here mean "this message cannot go the onboarding
    way", and neither gets better on a retry: a personal route dies when the pod
    is deleted, the person is removed from it, or the app is uninstalled.
    Uncaught, they left the inbox retrying a message that can never succeed and
    the person with no answer at all. Ordinary ingestion is the right next
    thing -- it routes by pod membership, and where it cannot it says so, which
    is the reply this was costing them.

    Except an expired setup form, which is neither: it is a submission of a
    form that stopped meaning anything, and ingesting it as a message would put
    a form's fields in front of the agent as though somebody had typed them.
    The person is told, and nothing is queued.
    """
    from app.modules.agent_surfaces.services.onboarding_inputs import (
        ExpiredOnboardingInput,
    )
    from app.modules.agent_surfaces.services.onboarding_private_delivery import (
        PrivateDeliveryUnavailable,
    )
    from app.modules.agent_surfaces.services.personal_dm_routes import (
        PersonalRouteUnavailable,
    )

    try:
        onboarding = await onboarding_handler(part)
    except ExpiredOnboardingInput:
        if tell is None:
            from app.modules.agent_surfaces.services.processing_failure_notice import (
                tell_sender as tell,
            )
        await tell(
            part,
            text=EXPIRED_FORM_TEXT,
            key=":expired-form",
            source=source,
            uow_factory=uow_factory,
            event_dedup_store=event_dedup_store or get_surface_event_dedup_store(),
        )
        return None
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


async def _release_claim_for_retry(
    context: AgentSurfaceContext,
    *,
    event: SurfaceWebhookReceivedEvent,
    event_dedup_store: SurfaceEventDedupStorePort,
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
    await release_ingress_claim(context, event_dedup_store=event_dedup_store)


async def _process_surface_webhook(
    event: SurfaceWebhookReceivedEvent,
    fs_logger: Logger,
    *,
    uow_factory: UnitOfWorkFactory,
    job_queue: SharedStreaqJobQueue,
    onboarding_handler: Callable[
        [SurfaceIngressRequest], Awaitable[OnboardingIngressResult]
    ]
    | None = None,
    # The process-wide store unless a caller has its own to hand: what a failed
    # enqueue gives its claim back to has to be the store the claim came from.
    event_dedup_store: SurfaceEventDedupStorePort | None = None,
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

    adapters = SurfacePlatformAdapterRegistry()
    # The bot added to a Telegram group through a Lemma link: its
    # `/start <code>` adopts the group and is never a question. Before any
    # session opens, because spending the code and saying hello are not queries.
    if await claim_telegram_group_join(uow_factory, ingress_request, adapters=adapters):
        return
    # A platform reporting that something we sent never arrived. Such a body
    # carries no message, so it is logged alongside the paths below, not instead.
    log_delivery_statuses(
        ingress_request.payload, source=event.source, adapters=adapters
    )

    async with uow_factory() as uow:
        handler = build_surface_ingress(uow)
        # Lifecycle events (the bot joined a channel, someone opened the app
        # home) are about the app itself: they never become a conversation, so
        # they are answered and stopped before the interaction/message paths.
        # Channel setup is time-critical: Slack expires the modal trigger in
        # ~3 seconds, so it runs before anything slower.
        app_events = build_app_event_handler(uow)
        if await app_events.try_handle_channel_setup(ingress_request):
            return

        if await app_events.try_handle_lifecycle(ingress_request):
            return
        # A group the bot asked WhatsApp to create, confirmed or refused. Never
        # instead of the message paths: such a body carries no message.
        await apply_group_updates(uow, ingress_request, adapters=adapters)

        # Split before anything reads a message out of it: a batch can hold a
        # button tap behind an ordinary message, and every parser reads only
        # the first one.
        deliveries = handler.split_webhook_deliveries(
            ingress_request, source=event.source
        )

    if onboarding_handler is None:
        from app.modules.agent_surfaces.services.chat_onboarding import (
            ChatOnboardingCoordinator,
        )

        onboarding_handler = ChatOnboardingCoordinator(uow_factory).handle
    await _enqueue_deliveries(
        deliveries,
        event,
        onboarding_handler=onboarding_handler,
        uow_factory=uow_factory,
        job_queue=job_queue,
        event_dedup_store=event_dedup_store,
    )


async def _enqueue_deliveries(
    deliveries: list[SurfaceIngressRequest],
    event: SurfaceWebhookReceivedEvent,
    *,
    onboarding_handler: Callable[
        [SurfaceIngressRequest], Awaitable[OnboardingIngressResult]
    ],
    uow_factory: UnitOfWorkFactory,
    job_queue: SharedStreaqJobQueue,
    event_dedup_store: SurfaceEventDedupStorePort | None,
    try_interaction: InteractionAttempt | None = None,
    tell_failed: Callable[..., Awaitable[None]] | None = None,
) -> None:
    """Every part on its own, and the delivery fails if any part did.

    One part failing used to stop the rest: part N raised, and parts N+1..
    waited for the retry -- which then met the same failure, if it was the
    part's own, every time until the delivery dead-lettered and took the
    healthy parts with it. Each part now succeeds or fails alone. A part that
    succeeded holds its claim and its job, so the retry the re-raise asks for
    finds it a duplicate and moves on; only the failed part is tried again.

    On the last attempt, the people whose messages failed are told. Nothing
    else would ever answer them.
    """
    failures: list[tuple[SurfaceIngressRequest, BaseException]] = []
    for index, part in enumerate(deliveries):
        # Gathered alone, not together: parts are one conversation's messages
        # in the order they were sent, and the jobs must be queued in it.
        (outcome,) = await asyncio.gather(
            _deliver_part(
                index,
                part,
                event,
                onboarding_handler=onboarding_handler,
                uow_factory=uow_factory,
                job_queue=job_queue,
                event_dedup_store=event_dedup_store,
                try_interaction=try_interaction or _try_interaction(uow_factory),
            ),
            return_exceptions=True,
        )
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, BaseException):
            failures.append((part, outcome))
    if not failures:
        return
    attempt = inbox_attempt()
    if attempt is not None and attempt.is_final:
        if tell_failed is None:
            from app.modules.agent_surfaces.services.processing_failure_notice import (
                tell_failed_senders as tell_failed,
            )
        await tell_failed(
            [part for part, _ in failures],
            source=event.source,
            uow_factory=uow_factory,
            event_dedup_store=event_dedup_store or get_surface_event_dedup_store(),
        )
    raise failures[0][1]


#: Resolves a part that is a native interaction (a tap); False for a message.
InteractionAttempt = Callable[[SurfaceIngressRequest], Awaitable[bool]]


def _try_interaction(uow_factory: UnitOfWorkFactory) -> InteractionAttempt:
    async def attempt(part: SurfaceIngressRequest) -> bool:
        async with uow_factory() as uow:
            return await build_surface_ingress(uow).try_handle_interaction(part)

    return attempt


async def _deliver_part(
    index: int,
    part: SurfaceIngressRequest,
    event: SurfaceWebhookReceivedEvent,
    *,
    onboarding_handler: Callable[
        [SurfaceIngressRequest], Awaitable[OnboardingIngressResult]
    ],
    uow_factory: UnitOfWorkFactory,
    job_queue: SharedStreaqJobQueue,
    event_dedup_store: SurfaceEventDedupStorePort | None,
    try_interaction: InteractionAttempt,
) -> None:
    """One message of a delivery: a tap that resumes a run, or a queued turn."""
    from app.modules.agent_surfaces.services.onboarding_inputs import (
        is_onboarding_input,
    )

    if not is_onboarding_input(part.payload) and await try_interaction(part):
        return
    context = await _context_for_delivery(
        part,
        onboarding_handler=onboarding_handler,
        uow_factory=uow_factory,
        source=event.source,
        event_dedup_store=event_dedup_store,
    )
    if not context:
        return
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
            await _release_claim_for_retry(
                context,
                event=event,
                event_dedup_store=event_dedup_store or get_surface_event_dedup_store(),
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
            await build_surface_service(uow).delete_all_surfaces_for_pod(parsed.pod_id)

    await inbox.process(
        "agent-surfaces.pod-deletion",
        event,
        process,
        max_attempts=CONSUMER_ATTEMPTS,
    )


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
        from app.modules.identity.contracts.onboarding import current_verified_phone

        parsed = UserMobileChangedEvent.model_validate(event)
        phone = await current_verified_phone(uow_factory, parsed.user_id)
        async with uow_factory() as uow:
            await ExternalSurfaceUserRepository(uow).clear_resolved_user(parsed.user_id)
            bindings = VerifiedSurfaceIdentityRepository(uow)
            await bindings.revoke_phone_bound_except(
                parsed.user_id, phone, previous_phone=parsed.previous_mobile_number
            )
            if phone is not None:
                # Proving a number takes it from whoever proved it before.
                await bindings.revoke_phone_held_by_others(parsed.user_id, phone)

    await inbox.process(
        "agent-surfaces.identity", event, process, max_attempts=CONSUMER_ATTEMPTS
    )


# Bounded by `delivery_limits`: a send that failed is already terminal here, so
# this only retries what happens before one.
@streaq_task(name="process_surface_message", max_tries=CONSUMER_ATTEMPTS)
async def process_surface_message(
    payload: dict,
):
    worker_ctx: AppWorkerContext = streaq_worker.context
    task_payload = SurfaceProcessMessageTaskPayload.model_validate(payload)
    # The starter scopes its own short UoWs (credential read + message-write
    # tail) around the long external I/O inside execute_chat — platform API
    # calls, file ingestion, and voice transcription — so no pooled DB
    # connection is held during that I/O.
    starter = build_surface_turn_starter(worker_ctx.uow_factory)
    finished = False
    try:
        await starter.execute_chat(task_payload.context)
        finished = True
    finally:
        # The job's last try failing is the message going unanswered for good;
        # the person is told rather than left looking at a sent message. A
        # `finally` and a flag rather than an `except`, so the failure itself
        # still propagates untouched to the retry machinery that logs it.
        from app.modules.agent_surfaces.services.processing_failure_notice import (
            final_job_attempt,
            tell_chat_sender_safely,
        )

        if not finished and final_job_attempt(CONSUMER_ATTEMPTS):
            await tell_chat_sender_safely(
                task_payload.context, uow_factory=worker_ctx.uow_factory
            )
