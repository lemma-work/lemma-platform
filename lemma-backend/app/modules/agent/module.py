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
    from app.modules.agent.api.controllers.conversation_open_controller import (
        router as conversation_open,
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
        # Before the general router: `/conversations/open` must not be read as
        # `/conversations/{conversation_id}`.
        conversation_open,
        conversation,
        widget_serve,
        widget,
    ]


def _event_routers():
    from app.modules.agent.events.handlers import router

    return [router]


module = LemmaModule(
    name="agent",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_report_system_model_pricing,),
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
