"""Schedule module registration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.registry import LemmaModule
from app.core.log.log import get_logger

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


def _register_work() -> None:
    # Every process: a pod deletion (API) writes this through `PodDeleted`.
    import app.modules.schedule.events.pod_deleted_work  # noqa: F401
    import app.modules.schedule.events.trigger_defaults_work  # noqa: F401

    # Every process: the API arms TIME fires and fires webhooks inline, the
    # worker runs them.
    import app.modules.schedule.work.registration  # noqa: F401

    # Every process: an agent run that ends (worker, or a stop in the API)
    # writes this through `AgentRunFinished`.
    import app.modules.schedule.work.agent_outcome  # noqa: F401

    # Every process: DATASTORE triggers are change-log subscribers; the
    # worker's `changes.*` items find them by prefix.
    import app.modules.schedule.services.datastore_event_handler  # noqa: F401


def _register_periodic_work() -> None:
    import app.modules.schedule.events.tasks  # noqa: F401


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
    register_periodic_work=_register_periodic_work,
    register_work=_register_work,
    worker_lifespans=(_reconcile_failure_breakers,),
)
