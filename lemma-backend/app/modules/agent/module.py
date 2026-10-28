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
    from app.modules.agent.agent_host.api import link_registry

    await link_registry.drain()


@asynccontextmanager
async def _warm_turn_imports(_context: object) -> AsyncIterator[None]:
    """Import what a turn reaches for before this worker claims one.

    The tokenizer starts building alongside (``warmed_for_turns`` says why it
    is not awaited). Left to the first turn, both ran on the event loop, and
    every turn beside it on the worker waited too. Skipped where nobody gains:
    in local mode, which the API skips for the same reason (the person waiting
    through the boot is usually the one about to send that turn), and in a
    worker that runs no turns, which has no use for the model clients.
    """
    from app.core.config import settings

    if settings.is_local_mode() or not _dispatches_turns():
        yield
        return
    from app.warm_imports import warmed_for_turns

    async with warmed_for_turns():
        yield


def _dispatches_turns() -> bool:
    """Whether this process claims either turn kind, given ``WORK_CLASSES``."""
    from app.core.infrastructure.work import registry
    from app.core.infrastructure.work.config import enabled_classes
    from app.modules.agent.events.work_kinds import AGENT_TURN_KINDS

    dispatched = set(enabled_classes())
    return any(
        (spec := registry.find(kind)) is not None and spec.cls in dispatched
        for kind in AGENT_TURN_KINDS
    )


def _routers():
    from app.modules.agent.api.controllers.agent_controller import router as agent
    from app.modules.agent.agent_host.api import routers as agent_host_routers
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
    from app.modules.agent.api.controllers.conversation_pending_controller import (
        router as conversation_pending,
    )
    from app.modules.agent.api.controllers.askable_pods_controller import (
        router as askable_pods,
    )
    from app.modules.agent.api.controllers.pod_links_controller import (
        router as pod_links,
    )

    # serve_router is included before the main widget router (more specific path).
    from app.modules.agent.api.controllers.widget_controller import (
        router as widget,
        serve_router as widget_serve,
    )

    return [
        agent,
        *agent_host_routers(),
        runtime_config,
        runtime_default,
        tool,
        # Ahead of `conversation`, whose `/{conversation_id}` would claim it.
        conversation_pending,
        conversation,
        conversation_queue,
        askable_pods,
        pod_links,
        widget_serve,
        widget,
    ]


def _live():
    from app.modules.agent.services.live_conversation import live_contribution

    return live_contribution()


def _register_analytics() -> None:
    # Every process: a memory file written anywhere -- including the shell's
    # `lemma files write`, which commits in the API -- drops the cached brief
    # section that quotes it, after the write commits.
    import app.modules.agent.events.memory_brief_invalidation  # noqa: F401


def _register_work() -> None:
    # Every process: a pod deletion (API) writes this through `PodDeleted`.
    import app.modules.agent.events.pod_links_lifecycle  # noqa: F401

    # Every process: a merge of two contacts (API, a web visitor proving an
    # email) writes this through `ContactMerged`.
    import app.modules.agent.events.contact_merged_work  # noqa: F401

    # Every process: the API starts and stops turns, the worker runs them and
    # their follow-ups (`NotificationSettled` included, emitted by surfaces).
    import app.modules.agent.events.run_followup_work  # noqa: F401
    import app.modules.agent.events.turn_work  # noqa: F401

    # Every process: a member's line in Lemma (API) and a run finishing
    # (worker) write these; a question closing anywhere settles its batch.
    # Importing the second also registers what answering a paused tool's
    # question on its line does (``answers_for("tool")``). A paused question
    # expiring (the channels' sweeper, worker) resumes its run.
    import app.modules.agent.events.channel_answer_work  # noqa: F401
    import app.modules.agent.events.channel_question_work  # noqa: F401
    import app.modules.agent.events.pause_expiry_work  # noqa: F401

    # Every process: a decision (API, surfaces) writes `agent.approval.execute`
    # and `wait_for` (worker) writes the wait items; both run on the worker.
    import app.modules.agent.events.approval_work  # noqa: F401
    import app.modules.agent.events.wait_work  # noqa: F401


def _register_periodic_work() -> None:
    import app.modules.agent.events.periodic_work  # noqa: F401


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
    live=_live,
    register_analytics=_register_analytics,
    register_work=_register_work,
    register_periodic_work=_register_periodic_work,
    api_lifespans=(_report_system_model_pricing, _drain_agent_host_links),
    # The worker is where agent runs actually dispatch, so a deployment
    # whose models cannot back its spend limit has to hear it there too.
    worker_lifespans=(_report_system_model_pricing, _warm_turn_imports),
)
