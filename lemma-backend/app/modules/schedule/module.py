"""Schedule module registration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.registry import LemmaModule
from app.core.log.log import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def _reconcile_failure_breakers(context) -> AsyncIterator[None]:
    from app.modules.schedule.services.run_outcome_service import (
        ScheduleRunOutcomeService,
    )

    async with context.uow_factory() as uow:
        count = await ScheduleRunOutcomeService(uow).reconcile_tripped_schedules()
    if count:
        logger.warning(
            "schedule.breakers.reconciled",
            deactivated_count=count,
        )
    yield


def _routers():
    from app.modules.schedule.api.controllers.schedule_controller import (
        router as schedule,
    )
    from app.modules.schedule.api.controllers.webhook_controller import (
        router as webhook,
    )

    return [schedule, webhook]


def _event_routers():
    # schedule_consumer also defines the `handle_llm_filter_task` streaq task,
    # which registers on import here (no separate register_streaq needed).
    from app.modules.schedule.handlers import (
        datastore_consumer,
        pod_lifecycle_consumer,
        schedule_consumer,
        schedule_lifecycle_consumer,
        schedule_notification_consumer,
    )
    from app.composition import schedule_target_outcome_consumer

    return [
        schedule_consumer.router,
        schedule_lifecycle_consumer.router,
        datastore_consumer.router,
        pod_lifecycle_consumer.router,
        schedule_notification_consumer.router,
        schedule_target_outcome_consumer.router,
    ]


module = LemmaModule(
    name="schedule",
    routers=_routers,
    event_routers=_event_routers,
    worker_lifespans=(_reconcile_failure_breakers,),
    stream_groups=(
        ("schedule_events", "schedule-notifications"),
        ("schedule_events", "schedule-runtime-lifecycle"),
        ("datastore.events", "schedule-datastore-events"),
        ("pod_events", "schedule-pod-events"),
        ("workflow_run_events", "schedule-workflow-outcomes"),
        ("agent_events", "schedule-agent-outcomes"),
    ),
)
