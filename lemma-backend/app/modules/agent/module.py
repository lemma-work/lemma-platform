"""Agent module registration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.registry import LemmaModule
from app.core.log.log import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def _report_system_model_pricing(
    _context: object,
) -> AsyncIterator[None]:
    from app.modules.agent.services.runtime_system_profiles import (
        system_lemma_openai_catalog_model_names,
    )
    from app.composition.agent_usage import (
        UsageService,
        assert_system_pricing_covers_catalog,
    )

    UsageService._load_environment_metadata()
    catalog = system_lemma_openai_catalog_model_names()
    unpriced = assert_system_pricing_covers_catalog(catalog)
    if unpriced:
        logger.debug("agent.module.system_lemma_models_will_be.observed")
    yield


def _routers():
    from app.modules.agent.api.controllers.agent_controller import router as agent
    from app.modules.agent.api.controllers.agent_host_controller import (
        router as agent_host,
    )
    from app.modules.agent.api.controllers.runtime_config_controller import (
        router as runtime_config,
    )
    from app.modules.agent.api.controllers.tool_controller import router as tool
    from app.modules.agent.api.controllers.conversation_controller import (
        router as conversation,
    )

    # serve_router is included before the main widget router (more specific path).
    from app.modules.agent.api.controllers.widget_controller import (
        router as widget,
        serve_router as widget_serve,
    )

    return [
        agent,
        agent_host,
        runtime_config,
        tool,
        conversation,
        widget_serve,
        widget,
    ]


@asynccontextmanager
async def _resume_parked_runs_on_start(context) -> AsyncIterator[None]:
    """Pick up runs the previous worker parked, without waiting for the sweep.

    The two-minute cron is the backstop; this is the common case. A deploy stops
    one worker and starts another, and the person watching the conversation
    should not sit through a cron interval to find out their work continues --
    measured at 74 seconds on the restart this was written for.

    Best-effort by design, like the schedule module's breaker reconciliation: a
    worker that boots ahead of its migrations must not crash-loop over a sweep
    that would simply run two minutes later.
    """
    from redis.exceptions import RedisError
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.domain.errors import DomainError
    from app.modules.agent.services.run_resume import resume_parked_agent_runs

    try:
        await resume_parked_agent_runs(
            uow_factory=context.uow_factory,
            job_queue=context.job_queue,
        )
    except DomainError, SQLAlchemyError, RedisError, OSError, TimeoutError:
        logger.warning(
            "agent.module.startup_resume_failed.degraded",
            exc_info=True,
        )
    yield


def _event_routers():
    from app.modules.agent.events.handlers import router

    return [router]


module = LemmaModule(
    name="agent",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_report_system_model_pricing,),
    worker_lifespans=(_resume_parked_runs_on_start,),
    stream_groups=(
        ("agent_events", "agent-events"),
        # A second group on the datastore's stream, so a memory file written
        # anywhere -- including the shell's `lemma files write`, which never
        # reaches this process -- drops the cached brief section that quotes it.
        # Declared here because publishers create declared groups before XADD;
        # an undeclared group silently misses everything published before its
        # first read.
        ("datastore.events", "agent-memory-brief-invalidation"),
    ),
)
