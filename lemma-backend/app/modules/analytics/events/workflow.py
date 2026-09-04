"""Workflows built, and workflow runs that reached a terminal state."""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import emit
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.analytics.events.wiring import (
    DELIVERED_STATUSES,
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
from app.modules.workflow.domain.events import (
    WORKFLOW_RUN_EVENTS_STREAM,
    WorkflowCreatedEvent,
    WorkflowRunTerminalEvent,
)

WIRED = frozenset({"workflow.created", "workflow_run.completed"})


@reliable_redis_stream_subscriber(
    router,
    WORKFLOW_RUN_EVENTS_STREAM,
    group="analytics-workflow",
    consumer="analytics-workflow-consumer",
)
async def on_workflow_event(
    event: dict[str, object],
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    event_type = event.get("event_type")
    if event_type not in {
        WorkflowCreatedEvent.get_event_type(),
        WorkflowRunTerminalEvent.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == WorkflowCreatedEvent.get_event_type():
            created = WorkflowCreatedEvent.model_validate(event)
            emit(
                "workflow.created",
                actor=actor_or_system(created.user_id),
                origin=origin,
                pod_id=created.pod_id,
                properties={
                    "pod_id": created.pod_id,
                    "workflow_id": created.workflow_id,
                    "node_count_bucket": bucket(created.node_count, COUNT_EDGES),
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
            actor=actor_or_system(terminal.user_id),
            origin=origin,
            pod_id=terminal.pod_id,
            properties={
                "pod_id": terminal.pod_id,
                "workflow_id": terminal.workflow_id,
                "status": terminal.status.value,
                "duration_bucket": seconds_bucket(
                    duration_seconds(terminal.started_at, terminal.completed_at)
                ),
            },
        )
        if terminal.status.value.upper() in DELIVERED_STATUSES:
            await maybe_emit_pod_delivered(
                uow_factory,
                pod_id=terminal.pod_id,
                organization_id=None,
                via=DeliveryVia.WORKFLOW_RUN,
                origin=origin,
                recipient_user_id=terminal.user_id,
                creator_user_id=await pod_creator(uow_factory, terminal.pod_id),
            )

    await inbox.process("analytics.workflow", event, record)
