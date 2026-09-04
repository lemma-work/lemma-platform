"""Schedules created, and unattended runs that finished."""

from __future__ import annotations

from faststream import Depends, Logger

from app.core.analytics import AnalyticsActor, emit
from app.core.authorization.context import ActorType
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.origin import OriginKind
from app.modules.analytics.events.wiring import (
    DELIVERED_STATUSES,
    origin_of,
    provide_uow_factory,
    router,
)
from app.modules.analytics.services.pod_delivery import (
    DeliveryVia,
    maybe_emit_pod_delivered,
)
from app.modules.schedule.domain.events.schedule import (
    ScheduleCreated,
    ScheduleRunCompleted,
)

WIRED = frozenset({"schedule.created", "schedule_run.completed"})


@reliable_redis_stream_subscriber(
    router,
    ScheduleCreated.stream_name(),
    group="analytics-schedule",
    consumer="analytics-schedule-consumer",
)
async def on_schedule_event(
    event: dict[str, object],
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
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
            await maybe_emit_pod_delivered(
                uow_factory,
                pod_id=completed.pod_id,
                organization_id=None,
                via=DeliveryVia.SCHEDULE_RUN,
                origin=origin,
                recipient_user_id=None,
                creator_user_id=None,
            )

    await inbox.process("analytics.schedule", event, record)
