"""Schedule module registration."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from app.core.registry import LemmaModule
from app.core.log.log import get_logger
from app.modules.schedule.config import schedule_settings

logger = get_logger(__name__)


@asynccontextmanager
async def _reconcile_failure_breakers(context) -> AsyncIterator[None]:
    """Catch up schedules whose failure streak passed the threshold while down.

    Best-effort by design. This is a backfill for state the breaker would have
    applied anyway on the next fire, so it must never decide whether the
    process starts: a worker that boots ahead of its migrations would otherwise
    crash-loop on a missing table, taking down every consumer in it over a
    reconciliation that could simply run next time.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.schedule.services.run_outcome_service import (
        ScheduleRunOutcomeService,
    )

    try:
        async with context.uow_factory() as uow:
            count = await ScheduleRunOutcomeService(uow).reconcile_tripped_schedules()
    except SQLAlchemyError:
        logger.warning("schedule.breakers.reconcile_skipped")
    else:
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
        target_outcome_consumer,
    )

    return [
        schedule_consumer.router,
        schedule_lifecycle_consumer.router,
        datastore_consumer.router,
        pod_lifecycle_consumer.router,
        schedule_notification_consumer.router,
        target_outcome_consumer.router,
    ]


def _register_streaq() -> None:
    import app.modules.schedule.events.tasks  # noqa: F401


@asynccontextmanager
async def _schedule_poller(context):
    """Fire due schedules and timers, for as long as the worker runs.

    Runs on every worker replica: the poll claims with `FOR UPDATE SKIP LOCKED`,
    so replicas share the work rather than duplicating it, and there is no
    leader to lose.

    The timers belong to other modules and arrive through their contracts. This
    used to be composed in `app/core`, whose comment said that was "where
    crossing module boundaries is the job" -- true while core was the
    composition root, which was deleted in #613.
    """
    from app.core.request_context import create_background_task
    from app.modules.agent.contracts.timers import claim_due_snooze_waits
    from app.modules.schedule.services.schedule_poller import run_schedule_poller
    from app.modules.workflow.contracts.timers import claim_due_workflow_waits

    task = create_background_task(
        run_schedule_poller(
            context.uow_factory,
            timer_claimers=(claim_due_workflow_waits, claim_due_snooze_waits),
            interval_seconds=schedule_settings.schedule_poll_interval_seconds,
        ),
        name="schedule-poller",
    )
    try:
        yield
    finally:
        task.cancel()
        # `CancelledError` is the only way out. The core worker also catches
        # `Exception` here and logs it, because it tears down a dozen unrelated
        # tasks and one of them dying its own way out must not stop the rest.
        # This tears down exactly one, whose loop already treats every
        # non-cancel exception as a degraded tick and keeps going -- so an
        # `except Exception` branch here would be unreachable, and a broad catch
        # that can never fire is worse than none.
        with suppress(asyncio.CancelledError):
            await task


module = LemmaModule(
    name="schedule",
    routers=_routers,
    event_routers=_event_routers,
    register_streaq=_register_streaq,
    worker_lifespans=(_reconcile_failure_breakers, _schedule_poller),
    stream_groups=(
        ("schedule_events", "schedule-notifications"),
        ("schedule_events", "schedule-runtime-lifecycle"),
        ("datastore.events", "schedule-datastore-events"),
        ("pod_events", "schedule-pod-events"),
        ("workflow_run_events", "schedule-workflow-outcomes"),
        ("agent_events", "schedule-agent-outcomes"),
    ),
)
