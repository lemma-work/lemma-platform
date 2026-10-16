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
    from app.modules.schedule.api.controllers.event_catalog_controller import (
        router as events,
    )
    from app.modules.schedule.api.controllers.webhook_controller import (
        router as webhook,
    )

    return [schedule, events, webhook]


def _event_routers():
    from app.modules.schedule.handlers import target_outcome_consumer

    return [target_outcome_consumer.router]


def _register_work() -> None:
    # Every process: a pod deletion (API) writes this through `PodDeleted`.
    import app.modules.schedule.events.pod_deleted_work  # noqa: F401
    import app.modules.schedule.events.trigger_defaults_work  # noqa: F401

    # Every process: the API arms TIME fires and fires webhooks inline, the
    # worker runs them.
    import app.modules.schedule.work.registration  # noqa: F401

    # Every process: DATASTORE triggers are change-log subscribers; the
    # worker's `changes.*` items find them by prefix.
    import app.modules.schedule.services.datastore_event_handler  # noqa: F401


def _register_streaq() -> None:
    import app.modules.schedule.events.tasks  # noqa: F401


@asynccontextmanager
async def _schedule_poller(context):
    """Fire due wait timers, for as long as the worker runs.

    TIME schedules and workflow timers are work items now. This loop stays
    only for agent ``wait_for`` TIME waits, which still live in their own table
    -- until PR 3 moves agent waits to ``agent.wait.fire`` items and deletes it.
    Runs on every worker replica: the poll claims with `FOR UPDATE SKIP LOCKED`,
    so replicas share the work rather than duplicating it, and there is no
    leader to lose.

    The timers belong to other modules and arrive through their contracts. This
    used to be composed in `app/core`, whose comment said that was "where
    crossing module boundaries is the job" -- true while core was the
    composition root, which was deleted in #613.
    """
    from app.core.request_context import create_background_task
    from app.modules.agent.contracts.timers import claim_due_waits
    from app.modules.schedule.services.schedule_poller import run_schedule_poller

    task = create_background_task(
        run_schedule_poller(
            context.uow_factory,
            timer_claimers=(claim_due_waits,),
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


def _resource_names():
    """How this module's resources are addressed by name in a grant.

    A thunk so the ORM import happens at assembly rather than whenever the
    module registry is imported. `app/core/authorization/resource_names.py`
    used to hold this table for every module at once.
    """
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.schedule.infrastructure.models.schedule import Schedule

    return (
        (
            ResourceType.SCHEDULE,
            ResourceNameTable(
                Schedule.id,
                Schedule.pod_id,
                Schedule.name,
                # Internal schedules are not name-addressable: they are created
                # by the platform, not named by a person, and a grant must not
                # be able to reach one.
                (Schedule.is_internal.is_(False),),
            ),
        ),
    )


module = LemmaModule(
    name="schedule",
    resource_names=_resource_names,
    routers=_routers,
    event_routers=_event_routers,
    register_streaq=_register_streaq,
    register_work=_register_work,
    worker_lifespans=(_reconcile_failure_breakers, _schedule_poller),
    stream_groups=(("agent_events", "schedule-agent-outcomes"),),
)
