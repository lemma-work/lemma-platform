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
from app.core.origin import OriginKind, origin_from_payload
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentCreatedEvent,
    AgentRunCompletedEvent,
    ConversationStartedEvent,
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
from app.modules.agent_surfaces.domain.events import (
    SurfaceConnectedEvent,
    SurfaceEvents,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.identity.domain.events import (
    IDENTITY_EVENTS_STREAM,
    OrganizationCreatedEvent,
    OrganizationMemberAddedEvent,
    UserSignedUpEvent,
)
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.pod.domain.events import (
    POD_EVENTS_STREAM,
    PodCreatedEvent,
    PodDeletedEvent,
    PodMemberAddedEvent,
)
from app.modules.schedule.domain.events.schedule import (
    ScheduleCreated,
    ScheduleRunCompleted,
)
from app.modules.workflow.domain.events import (
    WORKFLOW_RUN_EVENTS_STREAM,
    WorkflowCreatedEvent,
    WorkflowRunTerminalEvent,
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
        "organization.created",
        "organization.member_joined",
        "agent.created",
        "workflow.created",
        "schedule.created",
        "conversation.started",
        "workflow_run.completed",
        "schedule_run.completed",
        "surface.connected",
        "surface.message_answered",
    }
)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


#: Rebuild the origin the work arrived through, from the event itself. Shared
#: with the inbox, which binds the same value as a contextvar so handlers that
#: raise their own events inherit it.
_origin_of = origin_from_payload


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
TOKEN_EDGES = (1_000, 10_000, 100_000, 1_000_000)


def _range_bucket(
    value: float | None, edges: tuple[tuple[float, str], ...], overflow: str
) -> str | None:
    """Bucket a magnitude that starts at zero.

    Separate from ``_bucket`` because that one is count-shaped: its first label
    reads ``1-n``, which is wrong for a duration or a size, both of which can
    legitimately be zero.
    """
    if value is None:
        return None
    for edge, label in edges:
        if value <= edge:
            return label
    return overflow


_SECONDS_EDGES = ((1, "lt1s"), (5, "1-5s"), (30, "5-30s"), (120, "30-120s"), (600, "2-10m"))
_DAYS_EDGES = ((0, "same_day"), (7, "1-7d"), (30, "7-30d"), (90, "30-90d"), (365, "90-365d"))
_BYTES_EDGES = (
    (10_000, "lt10kb"),
    (100_000, "10-100kb"),
    (1_000_000, "100kb-1mb"),
    (10_000_000, "1-10mb"),
)


def _seconds_bucket(seconds: float | None) -> str | None:
    return _range_bucket(seconds, _SECONDS_EDGES, "10m_plus")


def _days_bucket(days: float | None) -> str | None:
    return _range_bucket(days, _DAYS_EDGES, "365d_plus")


def _bytes_bucket(size: int | None) -> str | None:
    return _range_bucket(size, _BYTES_EDGES, "10mb_plus")


def _duration_seconds(start, end) -> float | None:
    if start is None or end is None:
        return None
    return max((end - start).total_seconds(), 0.0)


#: Document kinds, as a closed set. Never the raw extension: an extension is
#: attacker-supplied, unbounded, and a cardinality problem.
_DOCUMENT_KINDS: dict[str, str] = {
    "csv": "sheet", "tsv": "sheet", "xls": "sheet", "xlsx": "sheet",
    "doc": "doc", "docx": "doc", "rtf": "doc", "odt": "doc",
    "txt": "doc", "md": "doc",
    "pdf": "pdf",
    "png": "image", "jpg": "image", "jpeg": "image", "gif": "image",
    "webp": "image", "svg": "image", "heic": "image",
    "mp3": "audio", "wav": "audio", "m4a": "audio", "ogg": "audio",
    "mp4": "video", "mov": "video", "webm": "video", "avi": "video",
    "py": "code", "ts": "code", "js": "code", "tsx": "code", "jsx": "code",
    "json": "code", "yaml": "code", "yml": "code", "sql": "code", "sh": "code",
}


def _document_kind(path: str | None) -> str:
    if not path or "." not in path:
        return "other"
    return _DOCUMENT_KINDS.get(path.rsplit(".", 1)[-1].strip().lower(), "other")


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
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        UserSignedUpEvent.get_event_type(),
        OrganizationCreatedEvent.get_event_type(),
        OrganizationMemberAddedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = _origin_of(event)
        if event_type == UserSignedUpEvent.get_event_type():
            parsed = UserSignedUpEvent.model_validate(event)
            # Note what is *not* forwarded: the event carries email and
            # first_name, and neither is in the allowlist for auth.signed_up.
            emit(
                "auth.signed_up",
                actor=AnalyticsActor.user(parsed.user_id),
                origin=origin,
            )
        elif event_type == OrganizationCreatedEvent.get_event_type():
            parsed_org = OrganizationCreatedEvent.model_validate(event)
            emit(
                "organization.created",
                actor=_actor_or_system(parsed_org.created_by_user_id),
                origin=origin,
                organization_id=parsed_org.organization_id,
            )
        else:
            parsed_member = OrganizationMemberAddedEvent.model_validate(event)
            # Counted here rather than carried on the event: a count written at
            # publish time is already stale by the time it is consumed.
            async with uow_factory() as uow:
                members = await OrganizationRepository(uow).list_members(
                    parsed_member.organization_id
                )
            emit(
                "organization.member_joined",
                actor=AnalyticsActor.user(parsed_member.user_id),
                origin=origin,
                organization_id=parsed_member.organization_id,
                properties={
                    "member_count_bucket": _bucket(len(members or ()), COUNT_EDGES)
                },
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
                    # The kind, from a closed set, never the raw extension.
                    "kind": _document_kind(parsed_file.path),
                    "size_bucket": _bytes_bucket(
                        (parsed_file.metadata or {}).get("size_bytes")
                        if isinstance(parsed_file.metadata, dict)
                        else None
                    ),
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
    event_type = event.get("event_type")
    if event_type not in {
        AgentRunCompletedEvent.get_event_type(),
        AgentCreatedEvent.get_event_type(),
        ConversationStartedEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = _origin_of(event)
        if event_type == AgentCreatedEvent.get_event_type():
            created = AgentCreatedEvent.model_validate(event)
            emit(
                "agent.created",
                actor=_actor_or_system(created.user_id),
                origin=origin,
                pod_id=created.pod_id,
                properties={
                    "pod_id": created.pod_id,
                    "agent_id": created.agent_id,
                    "tool_count_bucket": _bucket(created.tool_count, COUNT_EDGES),
                },
            )
            return

        if event_type == ConversationStartedEvent.get_event_type():
            started = ConversationStartedEvent.model_validate(event)
            # Sub-agent spawns and workflow agent nodes each open a conversation,
            # so counting every one measures traffic rather than people starting
            # work. Only top-level conversations are the product event.
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
                    "is_assistant": started.agent_id is None,
                },
            )
            return

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
            origin=origin,
            organization_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            properties={
                "pod_id": conversation.pod_id,
                "agent_id": conversation.agent_id,
                "conversation_id": parsed.conversation_id,
                "status": parsed.status.value,
                "duration_bucket": _seconds_bucket(
                    _duration_seconds(
                        getattr(conversation, "created_at", None), parsed.occurred_at
                    )
                ),
            },
        )

        # `surface.message_answered` is projected from here rather than from the
        # ingress service, which only *starts* the run and cannot know whether an
        # answer followed. Origin-pinned in the catalog, so it is pre-filtered
        # here: a surface conversation answered from the web UI is normal product
        # behaviour, and letting the emitter drop-and-log it would turn that into
        # a contract-violation alarm on every occurrence.
        if origin is None or origin.kind is not OriginKind.SURFACE:
            return
        async with uow_factory() as uow:
            link = await SurfaceConversationLinkRepository(uow).get_by_conversation_id(
                parsed.conversation_id
            )
        if link is None:
            return
        emit(
            "surface.message_answered",
            actor=AnalyticsActor.user(conversation.user_id),
            origin=origin,
            organization_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            properties={
                "pod_id": conversation.pod_id,
                "surface_id": link.surface_id,
                "agent_id": conversation.agent_id,
            },
        )

    await inbox.process("analytics.agent", event, record)


# -- schedule ---------------------------------------------------------------


@reliable_redis_stream_subscriber(
    router,
    ScheduleCreated.stream_name(),
    group="analytics-schedule",
    consumer="analytics-schedule-consumer",
)
async def on_schedule_event(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        ScheduleCreated.get_event_type(),
        ScheduleRunCompleted.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = _origin_of(event)
        if event_type == ScheduleCreated.get_event_type():
            created = ScheduleCreated.model_validate(event)
            emit(
                "schedule.created",
                actor=AnalyticsActor.user(created.user_id),
                origin=origin,
                pod_id=created.pod_id,
                properties={
                    "pod_id": created.pod_id,
                    "schedule_id": created.schedule_id,
                    "trigger_kind": created.schedule_type.value,
                },
            )
            return

        completed = ScheduleRunCompleted.model_validate(event)
        # Origin-pinned to SCHEDULE/DATA_TRIGGER in the catalog. A manual redrive
        # is request-backed and legitimately arrives some other way, so it is
        # filtered here rather than dropped-and-logged by the emitter.
        if origin is None or origin.kind not in {
            OriginKind.SCHEDULE,
            OriginKind.DATA_TRIGGER,
        }:
            return
        emit(
            "schedule_run.completed",
            actor=AnalyticsActor.autonomous(ActorType.SYSTEM),
            origin=origin,
            pod_id=completed.pod_id,
            properties={
                "pod_id": completed.pod_id,
                "schedule_id": completed.schedule_id,
                "status": completed.status,
            },
        )

    await inbox.process("analytics.schedule", event, record)


# -- workflow ---------------------------------------------------------------


@reliable_redis_stream_subscriber(
    router,
    WORKFLOW_RUN_EVENTS_STREAM,
    group="analytics-workflow",
    consumer="analytics-workflow-consumer",
)
async def on_workflow_event(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        WorkflowCreatedEvent.get_event_type(),
        WorkflowRunTerminalEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = _origin_of(event)
        if event_type == WorkflowCreatedEvent.get_event_type():
            created = WorkflowCreatedEvent.model_validate(event)
            emit(
                "workflow.created",
                actor=_actor_or_system(created.user_id),
                origin=origin,
                pod_id=created.pod_id,
                properties={
                    "pod_id": created.pod_id,
                    "workflow_id": created.workflow_id,
                    "node_count_bucket": _bucket(created.node_count, COUNT_EDGES),
                },
            )
            return

        terminal = WorkflowRunTerminalEvent.model_validate(event)
        # Absent on events published by a replica from before these fields
        # existed. Skipping is better than emitting a run with no pod, which
        # would be uncountable in either direction.
        if terminal.pod_id is None:
            return
        emit(
            "workflow_run.completed",
            actor=_actor_or_system(terminal.user_id),
            origin=origin,
            pod_id=terminal.pod_id,
            properties={
                "pod_id": terminal.pod_id,
                "workflow_id": terminal.workflow_id,
                "status": terminal.status.value,
                "duration_bucket": _seconds_bucket(
                    _duration_seconds(terminal.started_at, terminal.completed_at)
                ),
            },
        )

    await inbox.process("analytics.workflow", event, record)


# -- surfaces ---------------------------------------------------------------

#: Every agent gets a Resend mailbox provisioned automatically at creation, so
#: counting those as somebody connecting a surface would make inside reach track
#: the agent count instead of adoption.
_AUTO_PROVISIONED_SURFACES = frozenset({"RESEND"})


@reliable_redis_stream_subscriber(
    router,
    SurfaceEvents.STREAM,
    group="analytics-surface",
    consumer="analytics-surface-consumer",
)
async def on_surface_event(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != SurfaceConnectedEvent.get_event_type():
        return

    async def record() -> None:
        parsed = SurfaceConnectedEvent.model_validate(event)
        if parsed.platform.upper() in _AUTO_PROVISIONED_SURFACES:
            return
        emit(
            "surface.connected",
            actor=AnalyticsActor.autonomous(ActorType.SYSTEM),
            origin=_origin_of(event),
            pod_id=parsed.pod_id,
            properties={
                "pod_id": parsed.pod_id,
                "surface_id": parsed.surface_id,
            },
        )

    await inbox.process("analytics.surface", event, record)


def _actor_or_system(actor_id: UUID | None) -> AnalyticsActor:
    if actor_id is None:
        return AnalyticsActor.autonomous(ActorType.SYSTEM)
    return AnalyticsActor.user(actor_id)
