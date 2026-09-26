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
    from app.modules.usage.contracts.execution import (
        UsageService,
        assert_system_pricing_covers_catalog,
        unpriced_limit_policy,
        usage_limits_are_possible,
    )

    UsageService._load_environment_metadata()
    catalog = system_lemma_openai_catalog_model_names()
    unpriced = assert_system_pricing_covers_catalog(catalog)
    if unpriced and usage_limits_are_possible():
        # This check has always run and has always known the answer. It
        # reported it at `debug` with no fields, which `LOG_LEVEL=INFO` drops
        # before formatting -- so a deployment whose every request was about to
        # be refused for want of a price was told at boot, invisibly, and found
        # out from a 429 in the middle of a conversation instead.
        #
        # Only when a limit can actually apply. Unpriced models are unremarkable
        # otherwise: metering still records the tokens, and there is no budget
        # for the missing price to break.
        logger.warning(
            "agent.module.system_models_cannot_back_a_spend_limit.degraded",
            unpriced_models=",".join(sorted(unpriced)),
            unpriced_count=len(unpriced),
            policy=unpriced_limit_policy(),
        )
    elif unpriced:
        logger.debug("agent.module.system_lemma_models_will_be.observed")
    yield


@asynccontextmanager
async def _drain_agent_host_links(_context: object) -> AsyncIterator[None]:
    """Tell every host on this replica when to reconnect, as the API stops.

    See ``AgentHostLinkRegistry.drain`` for why this is usually a no-op under
    uvicorn, and why it is still the only place ``reconnect`` can come from.
    """
    yield
    from app.modules.agent.services.agent_host_link_registry import link_registry

    await link_registry.drain()


def _routers():
    from app.modules.agent.api.controllers.agent_controller import router as agent
    from app.modules.agent.api.controllers.agent_host_controller import (
        router as agent_host,
    )
    from app.modules.agent.api.controllers.agent_host_link_controller import (
        router as agent_host_link,
    )
    from app.modules.agent.api.controllers.agent_host_legacy_controller import (
        router as agent_host_legacy,
    )
    from app.modules.agent.api.controllers.runtime_config_controller import (
        router as runtime_config,
    )
    from app.modules.agent.api.controllers.runtime_default_controller import (
        router as runtime_default,
    )
    from app.modules.agent.api.controllers.tool_controller import router as tool
    from app.modules.agent.api.controllers.conversation_controller import (
        router as conversation,
    )
    from app.modules.agent.api.controllers.conversation_queue_controller import (
        router as conversation_queue,
    )

    # serve_router is included before the main widget router (more specific path).
    from app.modules.agent.api.controllers.widget_controller import (
        router as widget,
        serve_router as widget_serve,
    )

    return [
        agent,
        agent_host,
        agent_host_link,
        agent_host_legacy,
        runtime_config,
        runtime_default,
        tool,
        conversation,
        conversation_queue,
        widget_serve,
        widget,
    ]


def _event_routers():
    from app.modules.agent.events.handlers import router
    from app.modules.agent.events.notification_settled import (
        router as notification_settled_router,
    )

    return [router, notification_settled_router]


def _resource_names():
    """How this module's resources are addressed by name in a grant.

    A thunk so the ORM import happens at assembly rather than whenever the
    module registry is imported. `app/core/authorization/resource_names.py`
    used to hold this table for every module at once.
    """
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.agent.infrastructure.models import AgentModel

    return (
        (
            ResourceType.AGENT,
            ResourceNameTable(AgentModel.id, AgentModel.pod_id, AgentModel.name),
        ),
    )


module = LemmaModule(
    name="agent",
    resource_names=_resource_names,
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_report_system_model_pricing, _drain_agent_host_links),
    # The worker is where agent runs actually dispatch, so a deployment
    # whose models cannot back its spend limit has to hear it there too.
    worker_lifespans=(_report_system_model_pricing,),
    stream_groups=(
        ("agent_events", "agent-events"),
        # A second group on the datastore's stream, so a memory file written
        # anywhere -- including the shell's `lemma files write`, which never
        # reaches this process -- drops the cached brief section that quotes it.
        # Declared here because publishers create declared groups before XADD;
        # an undeclared group silently misses everything published before its
        # first read.
        ("datastore.events", "agent-memory-brief-invalidation"),
        # A group on surfaces' stream, so the last answer to an agent's asks
        # starts the asking conversation's next turn. Declared for the same
        # reason as the one above: a group nobody declared misses everything
        # published before it first reads.
        ("surface_events", "agent-notification-settled"),
    ),
)
