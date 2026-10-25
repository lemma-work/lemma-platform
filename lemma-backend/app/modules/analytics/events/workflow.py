"""Workflows built, and workflow runs that reached a terminal state."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import emit
from app.modules.analytics.events.wiring import (
    DELIVERED_STATUSES,
    ProjectionReads,
    actor_or_system,
    origin_of,
)
from app.modules.analytics.services.buckets import (
    COUNT_EDGES,
    bucket,
    duration_seconds,
    seconds_bucket,
)
from app.modules.analytics.services.pod_delivery import DeliveryVia
from app.modules.workflow.domain.events import (
    WorkflowCreatedEvent,
    WorkflowRunTerminalEvent,
)

WIRED = frozenset({"workflow.created", "workflow_run.completed"})


@projection(
    WorkflowCreatedEvent.get_event_type(),
    WorkflowRunTerminalEvent.get_event_type(),
)
async def on_workflow_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
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
                organization_id=await reads.organization_of_pod(created.pod_id),
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
        organization_id = await reads.organization_of_pod(terminal.pod_id)
        emit(
            "workflow_run.completed",
            actor=actor_or_system(terminal.user_id),
            origin=origin,
            organization_id=organization_id,
            pod_id=terminal.pod_id,
            properties={
                "pod_id": terminal.pod_id,
                "workflow_id": terminal.workflow_id,
                "status": terminal.status.value,
                "duration_bucket": seconds_bucket(
                    duration_seconds(terminal.started_at, terminal.completed_at)
                ),
                "waited_on_form": terminal.waited_on_form,
            },
        )
        if terminal.status.value.upper() in DELIVERED_STATUSES:
            await reads.deliver(
                pod_id=terminal.pod_id,
                organization_id=organization_id,
                via=DeliveryVia.WORKFLOW_RUN,
                origin=origin,
                recipient_user_id=terminal.user_id,
                creator_user_id=await reads.pod_creator(terminal.pod_id),
            )

    await record()
