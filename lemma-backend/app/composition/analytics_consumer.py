"""Project domain events onto the product-analytics catalog.

Lives in ``app/composition`` because it is inherently cross-module: it reads
identity, pod, datastore, function and agent events and emits one vocabulary.
No module owns that, and no module should have to import analytics to be
measured.

Why the bus and not the controllers (docs/design/product-analytics.md §5):

* instrumentation cannot drift from behaviour, because there is nothing to keep
  in sync -- no controller mentions analytics;
* an event that fires is an event that committed, since the outbox writes in
  the same transaction as the state change. Controller-level emits routinely
  report actions that later rolled back;
* every origin is covered by one implementation. A pod created from the CLI,
  from an agent over MCP, from a coding agent over ACP or from a workflow node
  all land on the same domain event.

``origin`` rides on the event itself (``DomainEvent.origin``), captured where
the work arrived. This consumer never infers it from its own surroundings --
it runs in a worker, where those surroundings say nothing about the caller.
"""

from __future__ import annotations

from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.analytics import AnalyticsActor, emit
from app.core.authorization.context import ActorType
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreFileCreatedEvent,
    DatastoreTableCreatedEvent,
)
from app.modules.function.domain.events import (
    FUNCTION_EVENTS_STREAM,
    FunctionCreatedEvent,
)
from app.modules.identity.domain.events import (
    IDENTITY_EVENTS_STREAM,
    UserSignedUpEvent,
)
from app.modules.pod.domain.events import (
    POD_EVENTS_STREAM,
    PodCreatedEvent,
    PodDeletedEvent,
    PodMemberAddedEvent,
)

router = RedisRouter()
logger = get_logger(__name__)


#: Catalog events this consumer actually raises today. The catalog is the
#: designed contract; this is reality, and ``test_analytics_wiring.py`` holds
#: the difference visible so an unwired event is a tracked gap rather than a
#: dashboard that is quietly always zero.
WIRED_EVENTS = frozenset(
    {
        "auth.signed_up",
        "pod.created",
        "pod.member_joined",
        "pod.deleted",
        "table.created",
        "document.added",
        "function.created",
        "agent_run.completed",
    }
)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def _origin_of(event: dict) -> Origin | None:
    """Rebuild the origin the work arrived through, from the event itself."""
    raw = event.get("origin")
    if not raw:
        return None
    try:
        kind = OriginKind(raw)
    except ValueError:
        return None
    return Origin(kind, platform=event.get("origin_platform"))


def _bucket(value: int | None, edges: tuple[int, ...]) -> str | None:
    """Bucket a count. Exact counts are a fingerprint and a cardinality
    problem; the shape of the distribution is what a decision needs."""
    if value is None:
        return None
    low = 0
    for edge in edges:
        if value <= edge:
            return f"{low}-{edge}" if low else f"1-{edge}"
        low = edge
    return f"{edges[-1]}plus"


COUNT_EDGES = (1, 5, 20, 100)


# -- identity ---------------------------------------------------------------


@reliable_redis_stream_subscriber(
    router,
    IDENTITY_EVENTS_STREAM,
    group="analytics-identity",
    consumer="analytics-identity-consumer",
)
async def on_identity_event(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != UserSignedUpEvent.get_event_type():
        return

    async def record() -> None:
        parsed = UserSignedUpEvent.model_validate(event)
        # Note what is *not* forwarded: the event carries email and first_name,
        # and neither is in the catalog's allowlist for auth.signed_up.
        emit(
            "auth.signed_up",
            actor=AnalyticsActor.user(parsed.user_id),
            origin=_origin_of(event),
        )

    await inbox.process("analytics.identity", event, record)


# -- pod --------------------------------------------------------------------


@reliable_redis_stream_subscriber(
    router,
    POD_EVENTS_STREAM,
    group="analytics-pod",
    consumer="analytics-pod-consumer",
)
async def on_pod_event(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        PodCreatedEvent.get_event_type(),
        PodMemberAddedEvent.get_event_type(),
        PodDeletedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = _origin_of(event)
        if event_type == PodCreatedEvent.get_event_type():
            parsed = PodCreatedEvent.model_validate(event)
            emit(
                "pod.created",
                actor=AnalyticsActor.user(parsed.creator_id),
                origin=origin,
                organization_id=parsed.organization_id,
                pod_id=parsed.pod_id,
                properties={"pod_id": parsed.pod_id},
            )
        elif event_type == PodMemberAddedEvent.get_event_type():
            parsed_member = PodMemberAddedEvent.model_validate(event)
            emit(
                "pod.member_joined",
                actor=AnalyticsActor.user(parsed_member.user_id),
                origin=origin,
                pod_id=parsed_member.pod_id,
                properties={"pod_id": parsed_member.pod_id},
            )
        else:
            parsed_deleted = PodDeletedEvent.model_validate(event)
            emit(
                "pod.deleted",
                actor=AnalyticsActor.autonomous(ActorType.SYSTEM),
                origin=origin,
                organization_id=parsed_deleted.organization_id,
                pod_id=parsed_deleted.pod_id,
                properties={"pod_id": parsed_deleted.pod_id},
            )

    await inbox.process("analytics.pod", event, record)


# -- datastore --------------------------------------------------------------


@reliable_redis_stream_subscriber(
    router,
    DATASTORE_EVENTS_STREAM,
    group="analytics-datastore",
    consumer="analytics-datastore-consumer",
)
async def on_datastore_event(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        DatastoreTableCreatedEvent.get_event_type(),
        DatastoreFileCreatedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = _origin_of(event)
        if event_type == DatastoreTableCreatedEvent.get_event_type():
            parsed = DatastoreTableCreatedEvent.model_validate(event)
            emit(
                "table.created",
                actor=_actor_or_system(parsed.actor_id),
                origin=origin,
                pod_id=parsed.pod_id,
                properties={"pod_id": parsed.pod_id, "table_id": parsed.table_id},
            )
        else:
            parsed_file = DatastoreFileCreatedEvent.model_validate(event)
            emit(
                "document.added",
                actor=_actor_or_system(parsed_file.actor_id),
                origin=origin,
                pod_id=parsed_file.pod_id,
                # `path` is deliberately not forwarded: it is a filename, and
                # filenames are business content.
                properties={
                    "pod_id": parsed_file.pod_id,
                    "document_id": parsed_file.file_id,
                },
            )

    await inbox.process("analytics.datastore", event, record)


# -- function ---------------------------------------------------------------


@reliable_redis_stream_subscriber(
    router,
    FUNCTION_EVENTS_STREAM,
    group="analytics-function",
    consumer="analytics-function-consumer",
)
async def on_function_event(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != FunctionCreatedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = FunctionCreatedEvent.model_validate(event)
        emit(
            "function.created",
            actor=AnalyticsActor.autonomous(ActorType.SYSTEM),
            origin=_origin_of(event),
            pod_id=parsed.pod_id,
            properties={"pod_id": parsed.pod_id, "function_id": parsed.function_id},
        )

    await inbox.process("analytics.function", event, record)


# -- agent ------------------------------------------------------------------


@reliable_redis_stream_subscriber(
    router,
    AGENT_EVENTS_STREAM,
    group="analytics-agent",
    consumer="analytics-agent-consumer",
)
async def on_agent_run_completed(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != AgentRunCompletedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = AgentRunCompletedEvent.model_validate(event)
        # The event carries no pod or organization -- it is scoped to a
        # conversation -- so read them, the same way the schedule outcome
        # consumer resolves its own target state.
        async with uow_factory() as uow:
            conversation = await ConversationRepository(uow).get_conversation(
                parsed.conversation_id
            )
        if conversation is None:
            return
        emit(
            "agent_run.completed",
            actor=AnalyticsActor.user(conversation.user_id),
            origin=_origin_of(event),
            organization_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            properties={
                "pod_id": conversation.pod_id,
                "agent_id": conversation.agent_id,
                "conversation_id": parsed.conversation_id,
                "status": parsed.status.value,
            },
        )

    await inbox.process("analytics.agent", event, record)


def _actor_or_system(actor_id: UUID | None) -> AnalyticsActor:
    if actor_id is None:
        return AnalyticsActor.autonomous(ActorType.SYSTEM)
    return AnalyticsActor.user(actor_id)
