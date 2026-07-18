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
    from app.modules.agent.services.runtime_profile_service import (
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


@asynccontextmanager
async def _close_agent_runtime_redis(_context: object) -> AsyncIterator[None]:
    try:
        yield
    finally:
        from app.modules.agent.infrastructure.daemon_hub import (
            close_agent_runtime_resources,
        )

        await close_agent_runtime_resources()


def _routers():
    from app.modules.agent.api.controllers.agent_controller import router as agent
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

    return [agent, runtime_config, tool, conversation, widget_serve, widget]


def _event_routers():
    from app.modules.agent.events.handlers import router

    return [router]


module = LemmaModule(
    name="agent",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(
        _report_system_model_pricing,
        _close_agent_runtime_redis,
    ),
    worker_lifespans=(_close_agent_runtime_redis,),
    stream_groups=(("agent_events", "agent-events"),),
)
