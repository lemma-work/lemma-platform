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

from collections.abc import Callable

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
from app.modules.agent.capabilities.open_notifications import (
    build_open_notifications_capability,
)
from app.modules.agent.capabilities.surface_platform import SurfacePlatformCapability
from app.modules.agent.capabilities.todo import TODO_TOOLSET_ID, TodoCapability
from app.modules.agent.capabilities.web_search import WebSearchCapability
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.prompts import (
    load_messaging_prompt,
    load_skills_prompt,
    load_speech_prompt,
    load_user_interaction_prompt,
    load_workspace_cli_prompt,
)
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.tools.graceful_toolset import GracefulToolset
from app.modules.agent.tools.registry import EXTRA_TOOLSET_OBJECTS
from app.modules.agent.tools.skills.pydantic_adapter import skills_toolset
from app.modules.agent.tools.user_interaction.pydantic_adapter import (
    user_interaction_toolset,
)
from app.modules.agent.tools.speech.pydantic_adapter import speech_toolset
from app.modules.agent.tools.messaging.pydantic_adapter import messaging_toolset
from app.modules.agent.tools.web.pydantic_adapter import web_search_toolset
from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
    is_workspace_cli_toolset,
)
from app.composition.agent_surface_runtime import platform_is_known

logger = get_logger(__name__)

_caching_capability_cls: type[PromptCachingCapability] = PromptCachingCapability


def configure_caching_capability(cls: type[PromptCachingCapability]) -> None:
    """Override the caching capability class used when prompt caching is enabled.

    Call at application startup (e.g. from a cloud module) to inject a
    provider-specific subclass (e.g. one that adds an ``x-session-affinity``
    header for Fireworks session routing).
    """
    global _caching_capability_cls
    _caching_capability_cls = cls

_EXTRA_TOOLSET_IDS = frozenset(id(obj) for obj in EXTRA_TOOLSET_OBJECTS)

# Toolsets whose usage guidance is part of their contract. Workspace CLI is
# matched by predicate rather than identity, so it is handled separately in
# ``_instructions_for``.
_INSTRUCTED_TOOLSETS: tuple[tuple[object, str, Callable[[], str]], ...] = (
    (skills_toolset, "skills", load_skills_prompt),
    (speech_toolset, "speech", load_speech_prompt),
    (messaging_toolset, "messaging", load_messaging_prompt),
    (user_interaction_toolset, "user_interaction", load_user_interaction_prompt),
)


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


def _instructions_for(toolset: object) -> tuple[str, Callable[[], str]] | None:
    """The usage guidance a toolset carries, whether it ends up visible or deferred.

    One lookup for both wrappers, because deferral is supposed to hide a
    toolset's *schemas*, never its *contract*. Messaging is the case that proves
    it: ``message_user`` does not pause the turn, so an agent that was never
    taught the send → snooze → check_messages loop sends a message and then sits
    waiting for a reply that arrives as a tool result never. Advertising the
    tool in the deferred hint while withholding that is the worst of both.
    """
    if is_workspace_cli_toolset(toolset):
        return "workspace_cli", load_workspace_cli_prompt
    for candidate, name, loader in _INSTRUCTED_TOOLSETS:
        if toolset is candidate:
            return name, loader
    return None


def _visible_capability(toolset: object) -> object:
    """Wrap one visible toolset as a capability.

    Toolsets that carry usage guidance get an instructions-bearing capability
    (web search and todo have bespoke ones; the rest use the generic
    ``InstructedToolsetCapability``); everything else is a plain toolset
    capability. Every wrapped toolset is made graceful first so a tool failure
    never crashes the run.
    """
    if toolset is web_search_toolset:
        return WebSearchCapability()
    if getattr(toolset, "id", None) == TODO_TOOLSET_ID:
        return TodoCapability(_graceful(toolset))
    guidance = _instructions_for(toolset)
    if guidance is None:
        return ToolsetCapability(_graceful(toolset))
    name, loader = guidance
    return InstructedToolsetCapability(
        _graceful(toolset), name=name, instructions_loader=loader
    )


def _deferred_capability(toolset: object) -> object:
    """Wrap one extra toolset as a deferred-loading capability.

    Graceful wrapping is applied INNER and deferral OUTER, so ``ToolSearch`` still
    sees the deferred-loading marker while tool failures stay graceful. The
    instructions ride along either way — ``defer_loading()`` returns an
    ``AbstractToolset``, so an instruction-bearing deferred capability is just
    the ordinary one wrapping a deferred toolset.
    """
    if not isinstance(toolset, AbstractToolset):
        return ToolsetCapability(toolset)  # pragma: no cover - defensive
    deferred = GracefulToolset(toolset).defer_loading()
    guidance = _instructions_for(toolset)
    if guidance is None:
        return ToolsetCapability(deferred)
    name, loader = guidance
    return InstructedToolsetCapability(
        deferred, name=name, instructions_loader=loader
    )


async def build_lemma_harness_tooling(
    *,
    uow_factory: UnitOfWorkFactory,
    agent: Agent,
    ctx: AgentContext,
    full_toolsets: list[object],
    agent_run_id: object,
    model_name: str,
    enable_prompt_caching: bool,
    protocol: RuntimeProfileProtocol = RuntimeProfileProtocol.OPENAI_COMPATIBLE,
) -> list[object]:
    """Return the full capability list for the in-process LEMMA harness."""
    with run_phase("capabilities") as span:
        capabilities = await _build_lemma_harness_tooling(
            uow_factory=uow_factory,
            agent=agent,
            ctx=ctx,
            full_toolsets=full_toolsets,
            agent_run_id=agent_run_id,
            model_name=model_name,
            enable_prompt_caching=enable_prompt_caching,
            protocol=protocol,
        )
        span.set_attribute("lemma.capabilities", len(capabilities))
        return capabilities


async def _build_lemma_harness_tooling(
    *,
    uow_factory: UnitOfWorkFactory,
    agent: Agent,
    ctx: AgentContext,
    full_toolsets: list[object],
    agent_run_id: object,
    model_name: str,
    enable_prompt_caching: bool,
    protocol: RuntimeProfileProtocol,
) -> list[object]:
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
    if surface_platform and platform_is_known(surface_platform):
        capabilities.append(SurfacePlatformCapability(str(surface_platform)))

    # Somebody may be waiting on the person this agent is talking to. Their next
    # message is often the answer, and without this the agent has no idea a
    # question is open or what the asker wanted done with the reply — so the
    # reply stays a chat message and the asking run waits forever.
    #
    # Appended AFTER the caching-sensitive fragments above and rebuilt each run
    # on purpose: unlike per-platform guidance, this changes the moment somebody
    # answers, and a cached copy would have the agent chasing a closed question.
    open_notifications = await build_open_notifications_capability(
        ctx.conversation_id
    )
    if open_notifications is not None:
        capabilities.append(open_notifications)

    if enable_prompt_caching:
        capabilities.append(
            _caching_capability_cls(
                conversation_id=ctx.conversation_id, protocol=protocol
            )
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
