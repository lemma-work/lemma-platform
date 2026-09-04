"""Agents built, conversations opened, and runs that finished.

The one consumer that needs more than the event in front of it. A run is scoped
to a conversation, so the pod, organization, agent and person behind it used to
be re-read here on every completion. They are now captured on the event where
they already exist, and the read is the fallback -- see ``_run_scope``.
"""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import AnalyticsActor, emit
from app.core.authorization.delegation import is_pod_default_agent
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.origin import Origin, OriginKind
from app.modules.agent.contracts.conversations import (
    ConversationScope,
    conversation_scope,
)
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentCreatedEvent,
    AgentRunCompletedEvent,
    ConversationStartedEvent,
)
from app.modules.agent_surfaces.contracts.conversations import (
    surface_id_for_conversation,
)
from app.modules.analytics.events.wiring import (
    actor_or_system,
    origin_of,
    provide_uow_factory,
    router,
)
from app.modules.analytics.services.buckets import (
    COUNT_EDGES,
    bucket,
    duration_seconds,
    seconds_bucket,
)
from app.modules.analytics.services.pod_delivery import (
    DeliveryVia,
    maybe_emit_pod_delivered,
    pod_creator,
)

WIRED = frozenset(
    {
        "agent.created",
        "conversation.started",
        "agent_run.completed",
        "surface.message_answered",
    }
)


@reliable_redis_stream_subscriber(
    router,
    AGENT_EVENTS_STREAM,
    group="analytics-agent",
    consumer="analytics-agent-consumer",
)
async def on_agent_event(
    event: dict[str, object],
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        AgentRunCompletedEvent.get_event_type(),
        AgentCreatedEvent.get_event_type(),
        ConversationStartedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == AgentCreatedEvent.get_event_type():
            _record_agent_created(AgentCreatedEvent.model_validate(event), origin)
            return
        if event_type == ConversationStartedEvent.get_event_type():
            _record_conversation_started(
                ConversationStartedEvent.model_validate(event), origin
            )
            return
        await _record_run_completed(
            uow_factory, AgentRunCompletedEvent.model_validate(event), origin
        )

    await inbox.process("analytics.agent", event, record)


def _record_agent_created(created: AgentCreatedEvent, origin: Origin | None) -> None:
    emit(
        "agent.created",
        actor=actor_or_system(created.user_id),
        origin=origin,
        pod_id=created.pod_id,
        properties={
            "pod_id": created.pod_id,
            "agent_id": created.agent_id,
            "tool_count_bucket": bucket(created.tool_count, COUNT_EDGES),
        },
    )


def _record_conversation_started(
    started: ConversationStartedEvent, origin: Origin | None
) -> None:
    # Sub-agent spawns and workflow agent nodes each open a conversation, so
    # counting every one measures traffic rather than people starting work. Only
    # top-level conversations are the product event.
    if started.parent_id is not None:
        return
    emit(
        "conversation.started",
        actor=AnalyticsActor.user(started.user_id),
        origin=origin,
        pod_id=started.pod_id,
        properties={
            "pod_id": started.pod_id,
            "conversation_id": started.conversation_id,
            "agent_id": started.agent_id,
            "is_assistant": is_pod_default_agent(
                started.agent_id, pod_id=started.pod_id
            ),
        },
    )


async def _run_scope(
    uow_factory, parsed: AgentRunCompletedEvent
) -> ConversationScope | None:
    """Where this run happened: which pod, organization, agent and person.

    Carried on the event by the finalizer, which builds it from the same
    ``RunIdentity`` it already holds. Absent on two kinds of event, and both
    still have to be measured, which is why the fallback stays: an event
    published before those fields existed and redelivered afterwards, and a run
    ended by the stop handler or one of the status sweeps, neither of which has
    a live run context to copy from.
    """
    if parsed.pod_id is not None and parsed.user_id is not None:
        return ConversationScope(
            user_id=parsed.user_id,
            pod_id=parsed.pod_id,
            organization_id=parsed.organization_id,
            agent_id=parsed.agent_id,
        )
    async with uow_factory() as uow:
        return await conversation_scope(uow, parsed.conversation_id)


async def _record_run_completed(
    uow_factory, parsed: AgentRunCompletedEvent, origin: Origin | None
) -> None:
    scope = await _run_scope(uow_factory, parsed)
    if scope is None:
        return
    emit(
        "agent_run.completed",
        actor=AnalyticsActor.user(scope.user_id),
        origin=origin,
        organization_id=scope.organization_id,
        pod_id=scope.pod_id,
        properties={
            "pod_id": scope.pod_id,
            "agent_id": scope.agent_id,
            "conversation_id": parsed.conversation_id,
            "status": parsed.status.value,
            # The run's own duration. This used to be measured from the
            # conversation's creation, which is the only start time the consumer
            # could see, and so reported the age of the thread on every turn
            # after the first. Absent on the events `_run_scope` falls back for:
            # a sweep that ended an abandoned run knows when it noticed, not when
            # the run began, and inventing the difference is worse than no value.
            "duration_bucket": seconds_bucket(
                duration_seconds(parsed.started_at, parsed.occurred_at)
            ),
        },
    )

    creator_user_id = await pod_creator(uow_factory, scope.pod_id)
    await maybe_emit_pod_delivered(
        uow_factory,
        pod_id=scope.pod_id,
        organization_id=scope.organization_id,
        via=DeliveryVia.AGENT_RUN,
        origin=origin,
        recipient_user_id=scope.user_id,
        creator_user_id=creator_user_id,
    )

    # `surface.message_answered` is projected from here rather than from the
    # ingress service, which only *starts* the run and cannot know whether an
    # answer followed. Origin-pinned in the catalog, so it is pre-filtered here:
    # a surface conversation answered from the web UI is normal product
    # behaviour, and letting the emitter drop-and-log it would turn that into a
    # contract-violation alarm on every occurrence.
    if origin is None or origin.kind is not OriginKind.SURFACE:
        return
    async with uow_factory() as uow:
        surface_id = await surface_id_for_conversation(uow, parsed.conversation_id)
    if surface_id is None:
        return
    emit(
        "surface.message_answered",
        actor=AnalyticsActor.user(scope.user_id),
        origin=origin,
        organization_id=scope.organization_id,
        pod_id=scope.pod_id,
        properties={
            "pod_id": scope.pod_id,
            "surface_id": surface_id,
            "agent_id": scope.agent_id,
        },
    )
    await maybe_emit_pod_delivered(
        uow_factory,
        pod_id=scope.pod_id,
        organization_id=scope.organization_id,
        via=DeliveryVia.SURFACE_MESSAGE,
        origin=origin,
        recipient_user_id=scope.user_id,
        creator_user_id=creator_user_id,
    )
