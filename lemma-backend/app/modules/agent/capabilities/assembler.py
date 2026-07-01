"""Assemble the pydantic-ai capability list for the LEMMA harness.

Every tool surface the in-process agent runs with is realized as a *capability*
— uniformly for the pod-default assistant and user-created agents:
  * visible toolsets (workspace_cli, web_search, skills, subagents,
    user_interaction, granted function_*/agent_*, surface tools) → one capability
    each (web search additionally contributes its usage prompt),
  * deferred-extra toolsets (pod/subagents) → kept in-process but hidden behind
    ``ToolSearch`` via ``.defer_loading()`` so their schemas never enter the
    prompt prefix (smaller context, preserved prompt caching); the model reveals
    them on demand through the local ``search_tools`` tool,
  * behavioural capabilities (current-time, prompt-caching, todo).

The extra tools run in-process — the toolset objects already live in this worker,
so routing them back through the HTTP MCP server would only add a self-call with
no benefit. External harnesses (Codex/Claude-Code) are separate processes and
still reach the same tools through the per-conversation MCP server unchanged.
"""

from __future__ import annotations

from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.capabilities import Toolset as ToolsetCapability
from pydantic_ai.toolsets import AbstractToolset

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.capabilities.current_time import CurrentTimeCapability
from app.modules.agent.capabilities.deferred_hint import (
    DeferredToolsHintCapability,
    build_deferred_tools_hint,
)
from app.modules.agent.capabilities.instructed_toolset import (
    InstructedToolsetCapability,
)
from app.modules.agent.capabilities.prompt_caching import PromptCachingCapability

_caching_capability_cls: type[PromptCachingCapability] = PromptCachingCapability


def configure_caching_capability(cls: type[PromptCachingCapability]) -> None:
    """Override the caching capability class used when prompt caching is enabled.

    Call at application startup (e.g. from a cloud module) to inject a
    provider-specific subclass (e.g. one that adds an ``x-session-affinity``
    header for Fireworks session routing).
    """
    global _caching_capability_cls
    _caching_capability_cls = cls
from app.modules.agent.capabilities.surface_platform import SurfacePlatformCapability
from app.modules.agent.capabilities.todo import TODO_TOOLSET_ID, TodoCapability
from app.modules.agent.capabilities.web_search import WebSearchCapability
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.prompts import (
    load_skills_prompt,
    load_speech_prompt,
    load_workspace_cli_prompt,
)
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.graceful_toolset import GracefulToolset
from app.modules.agent.tools.registry import EXTRA_TOOLSET_OBJECTS
from app.modules.agent.tools.skills.pydantic_adapter import skills_toolset
from app.modules.agent.tools.speech.pydantic_adapter import speech_toolset
from app.modules.agent.tools.web.pydantic_adapter import web_search_toolset
from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
    is_workspace_cli_toolset,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)

logger = get_logger(__name__)

_EXTRA_TOOLSET_IDS = frozenset(id(obj) for obj in EXTRA_TOOLSET_OBJECTS)


def _agent_has_toolset(agent: Agent, toolset: AgentToolset) -> bool:
    for name in agent.toolsets:
        try:
            if AgentToolset(name) == toolset:
                return True
        except ValueError:  # pragma: no cover - defensive
            continue
    return False


def _partition_core_extra(
    toolsets: list[object],
    *,
    is_pod_default: bool,
) -> tuple[list[object], list[object]]:
    """Split toolsets into core (prompt-visible) vs extra (deferred via ToolSearch).

    Deferral only applies to the pod-default agent, which otherwise accumulates
    every optional toolset in its prompt prefix. User-created agents already
    chose a deliberately scoped toolset, so POD/SUBAGENTS are injected directly
    for them like any other configured toolset.
    """
    if not is_pod_default:
        return list(toolsets), []
    core: list[object] = []
    extra: list[object] = []
    for toolset in toolsets:
        (extra if id(toolset) in _EXTRA_TOOLSET_IDS else core).append(toolset)
    return core, extra


def _graceful(toolset: object) -> object:
    """Wrap a toolset so a raising tool body returns an error instead of aborting.

    Identity checks in the assembler run on the RAW toolset before this wraps it,
    so partitioning/dispatch are unaffected.
    """
    if isinstance(toolset, AbstractToolset):
        return GracefulToolset(toolset)
    return toolset  # pragma: no cover - defensive


def _visible_capability(toolset: object) -> object:
    """Wrap one visible toolset as a capability.

    Toolsets that carry usage guidance get an instructions-bearing capability
    (web search and todo have bespoke ones; workspace CLI and skills use the
    generic ``InstructedToolsetCapability``); everything else is a plain toolset
    capability. Every wrapped toolset is made graceful first so a tool failure
    never crashes the run.
    """
    if toolset is web_search_toolset:
        return WebSearchCapability()
    if is_workspace_cli_toolset(toolset):
        return InstructedToolsetCapability(
            _graceful(toolset),
            name="workspace_cli",
            instructions_loader=load_workspace_cli_prompt,
        )
    if toolset is skills_toolset:
        return InstructedToolsetCapability(
            _graceful(toolset),
            name="skills",
            instructions_loader=load_skills_prompt,
        )
    if toolset is speech_toolset:
        return InstructedToolsetCapability(
            _graceful(toolset),
            name="speech",
            instructions_loader=load_speech_prompt,
        )
    if getattr(toolset, "id", None) == TODO_TOOLSET_ID:
        return TodoCapability(_graceful(toolset))
    return ToolsetCapability(_graceful(toolset))


def _deferred_capability(toolset: object) -> object:
    """Wrap one extra toolset as a deferred-loading capability.

    Graceful wrapping is applied INNER and deferral OUTER, so ``ToolSearch`` still
    sees the deferred-loading marker while tool failures stay graceful.
    """
    if isinstance(toolset, AbstractToolset):
        return ToolsetCapability(GracefulToolset(toolset).defer_loading())
    return ToolsetCapability(toolset)  # pragma: no cover - defensive


async def build_lemma_harness_tooling(
    *,
    uow_factory: UnitOfWorkFactory,
    agent: Agent,
    ctx: AgentContext,
    full_toolsets: list[object],
    agent_run_id: object,
    model_name: str,
    enable_prompt_caching: bool,
) -> list[object]:
    """Return the full capability list for the in-process LEMMA harness."""
    # agent/uow_factory/run-id reserved: tool selection (incl. todo) now happens in
    # RunToolAssembler, so full_toolsets already reflects the agent's toolsets.
    _ = (agent, uow_factory, agent_run_id, model_name)
    core, extra = _partition_core_extra(
        full_toolsets, is_pod_default=ctx.is_pod_default_agent
    )

    # The todo toolset (if the agent has TODO) already arrives in `full_toolsets`
    # from RunToolAssembler and is wrapped by `_visible_capability` above.
    capabilities: list[object] = [_visible_capability(obj) for obj in core]
    capabilities.append(CurrentTimeCapability())

    # When the run is on a third-party surface, append standing per-platform
    # guidance (delivery/forms/formatting/channel-context). Stable per
    # conversation, so it rides in the cached prefix alongside the other
    # instruction-bearing capabilities.
    surface_platform = getattr(ctx, "surface_platform", None)
    if surface_platform and get_platform_capabilities(surface_platform) is not None:
        capabilities.append(SurfacePlatformCapability(str(surface_platform)))

    if enable_prompt_caching:
        capabilities.append(
            _caching_capability_cls(conversation_id=ctx.conversation_id)
        )

    if extra:
        # Tool search reveals the deferred extra tools on demand (provider-native
        # on Anthropic/OpenAI, a local search_tools function on Fireworks).
        capabilities.append(ToolSearch())
        capabilities.extend(_deferred_capability(obj) for obj in extra)
        # ...and a static hint so the model knows those tools exist to search for.
        hint = build_deferred_tools_hint(extra)
        if hint:
            capabilities.append(DeferredToolsHintCapability(hint))

    return capabilities
