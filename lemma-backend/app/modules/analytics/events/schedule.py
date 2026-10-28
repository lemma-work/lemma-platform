"""Schedules created, and unattended runs that finished."""

from __future__ import annotations

from app.core.analytics.domain_events import projection
from app.core.analytics import ANALYTICS_CATALOG, AnalyticsActor, emit
from app.core.authorization.context import ActorType
from app.modules.analytics.events.wiring import (
    DELIVERED_STATUSES,
    ProjectionReads,
    origin_of,
)
from app.modules.analytics.services.pod_delivery import DeliveryVia
from app.modules.schedule.domain.events.schedule import (
    ScheduleCreated,
    ScheduleRunCompleted,
)

WIRED = frozenset({"schedule.created", "schedule_run.completed"})

#: Origins a schedule run arrives through, one per schedule type: the catalog's
#: pin on ``schedule_run.completed``, read rather than restated.
_FIRED = ANALYTICS_CATALOG["schedule_run.completed"].origins or frozenset()


@projection(
    ScheduleCreated.get_event_type(),
    ScheduleRunCompleted.get_event_type(),
)
async def on_schedule_event(
    event: dict[str, object],
    reads: ProjectionReads | None = None,
) -> None:
    reads = reads or ProjectionReads()
    event_type = event.get("event_type")
    if event_type not in {
        ScheduleCreated.get_event_type(),
        ScheduleRunCompleted.get_event_type(),
    }:
        return

    async def record() -> None:
        origin = origin_of(event)
        if event_type == ScheduleCreated.get_event_type():
            created = ScheduleCreated.model_validate(event)
            emit(
                "schedule.created",
                actor=AnalyticsActor.user(created.user_id),
                origin=origin,
                organization_id=await reads.organization_of_pod(created.pod_id),
                pod_id=created.pod_id,
                properties={
                    "pod_id": created.pod_id,
                    "schedule_id": created.schedule_id,
                    "trigger_kind": created.schedule_type.value,
                },
            )
            return

        completed = ScheduleRunCompleted.model_validate(event)
        # Origin-pinned in the catalog. A redrive somebody asked for carries no
        # origin (``schedule_run_origin``), so it is filtered here rather than
        # dropped-and-logged by the emitter.
        if origin is None or origin.kind not in _FIRED:
            return
        organization_id = await reads.organization_of_pod(completed.pod_id)
        emit(
            "schedule_run.completed",
            # Delegated, not autonomous: the run is unattended but it is done for
            # somebody, and `DELEGATED_USER_WORKLOAD` is exactly the case where
            # the work belongs on a human's timeline while staying
            # distinguishable from what they did themselves.
            actor=(
                AnalyticsActor.delegated(delegated_by_user_id=completed.user_id)
                if completed.user_id
                else AnalyticsActor.autonomous(ActorType.SYSTEM)
            ),
            origin=origin,
            organization_id=organization_id,
            pod_id=completed.pod_id,
            properties={
                "pod_id": completed.pod_id,
                "schedule_id": completed.schedule_id,
                "status": completed.status,
            },
        )
        if (
            completed.pod_id is not None
            and completed.status.upper() in DELIVERED_STATUSES
        ):
            # Branch (b): autonomous work delivers without a recipient test. A
            # scheduled report nobody watches is the design's own example of a
            # pod earning its keep.
            await reads.deliver(
                pod_id=completed.pod_id,
                organization_id=organization_id,
                via=DeliveryVia.SCHEDULE_RUN,
                origin=origin,
                recipient_user_id=None,
                creator_user_id=None,
                delegated_by_user_id=completed.user_id,
            )

    await record()
