"""The pydantic-ai capabilities the in-process harness runs with.

``harness_capabilities`` turns a run's ToolSpecs into toolsets -- one per group,
each beside its usage guidance; on the pod default assistant the deferrable
ones behind ``ToolSearch`` with a hint naming them -- and layers on the hooks
that are pydantic-ai's to run: the run's context part, steering, provider
caching, call-id dedup and the lease gate.

Order matters twice. Instructions are emitted in capability order, so the
stable guidance forms the cached prefix; and nothing here contributes per-run
text to it (that is the context part, next to the turn).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentCapability,
    ToolSearch,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.capabilities import Toolset as ToolsetCapability
from pydantic_ai.messages import ModelResponse, ModelResponsePart, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.tools import ToolDefinition

from app.core.infrastructure.work import LeaseLost
from app.core.log.log import get_logger
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.domain.prompt.deferred_tools import deferred_tools_hint
from app.modules.agent.domain.prompt.run_context import run_context_for
from app.modules.agent.domain.prompt.surface import (
    is_group_conversation,
    surface_platform_guidance,
)
from app.modules.agent.domain.prompt.templates import (
    load_memory_prompt,
    toolset_instructions,
)
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.tool_call_ids import (
    call_signature,
    unique_per_call_id,
    unparsed_signature,
)
from app.modules.agent.domain.tool_profile import ToolProfile
from app.modules.agent.domain.tools import ToolGroup, ToolSpec
from app.modules.agent.infrastructure.harness.lemma.history_parts import (
    RunContextCapability,
)
from app.modules.agent.infrastructure.harness.lemma.pending_user_messages import (
    ClaimSteered,
    OnSteered,
    PendingUserMessagesCapability,
)
from app.modules.agent.infrastructure.harness.lemma.toolset_adapter import (
    function_toolset,
)
from app.modules.agent.infrastructure.tools.deferred.tools import (
    deferred_entry_specs,
)
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.services.tool_runtime import ToolRuntime
from app.modules.agent_surfaces.contracts.platforms import platform_is_known

logger = get_logger(__name__)

# Anthropic's cache TTL: the short window covers a person's active
# back-and-forth without paying the long-lived tier's premium.
_ANTHROPIC_CACHE_TTL = "5m"


def harness_capabilities(
    *,
    ctx: ToolContext,
    specs: Sequence[ToolSpec],
    runtime: ToolRuntime,
    enable_prompt_caching: bool,
    protocol: RuntimeProfileProtocol = RuntimeProfileProtocol.OPENAI_COMPATIBLE,
    claim_steered: ClaimSteered | None = None,
    on_steered: OnSteered | None = None,
    reveals_tools_natively: bool = True,
    steer_due: Callable[[], bool] | None = None,
) -> list[AgentCapability[ToolContext]]:
    """Every capability an in-process run gets, its tools included.

    ``reveals_tools_natively`` says the model has a channel for tools added
    mid-run that leaves the request's ``tools`` as they were; without one, the
    deferred tools are reached through two fixed entries instead
    (``infrastructure/tools/deferred``).
    """
    with run_phase("capabilities") as span:
        capabilities = _capabilities(
            ctx=ctx,
            specs=specs,
            runtime=runtime,
            enable_prompt_caching=enable_prompt_caching,
            protocol=protocol,
            steering=(claim_steered, on_steered, steer_due),
            reveals_tools_natively=reveals_tools_natively,
        )
        span.set_attribute("lemma.capabilities", len(capabilities))
        return capabilities


def _capabilities(
    *,
    ctx: ToolContext,
    specs: Sequence[ToolSpec],
    runtime: ToolRuntime,
    enable_prompt_caching: bool,
    protocol: RuntimeProfileProtocol,
    steering: tuple[
        ClaimSteered | None, OnSteered | None, Callable[[], bool] | None
    ] = (None, None, None),
    reveals_tools_natively: bool = True,
) -> list[AgentCapability[ToolContext]]:
    groups = _groups(spec for spec in specs if spec.toolset is not ToolGroup.QUESTIONS)
    # Deferral is for the pod's own assistant, which accumulates every optional
    # toolset; a named agent chose its own. Never on a stranger's run, whose
    # one way past what is Public is reaching the member, in view.
    defers = ctx.is_pod_default_agent and not run_audience(ctx).answers_outsiders
    shown, deferred = _arm(
        groups,
        ToolProfile(ctx.tool_profile) if defers else ToolProfile.TODAY,
        defers=defers,
        natively=reveals_tools_natively,
    )
    host_execution = ctx.host_workspace is not None

    def with_guidance(
        group: Sequence[ToolSpec], toolset: AbstractToolset[ToolContext]
    ) -> AgentCapability[ToolContext]:
        guidance = toolset_instructions(group[0].toolset, host_execution=host_execution)
        if guidance is None:
            return ToolsetCapability(toolset)
        return InstructedToolset(toolset, guidance)

    capabilities: list[AgentCapability[ToolContext]] = [
        with_guidance(group, function_toolset(group, runtime)) for group in shown
    ]
    capabilities.append(UniqueToolCallIds())
    capabilities.append(RunContextCapability(run_context_for(ctx)))
    capabilities.extend(_run_hooks(ctx, *steering))
    if deferred:
        capabilities.extend(
            _deferred(
                deferred,
                runtime,
                natively=reveals_tools_natively,
                host_execution=host_execution,
                guided={group[0].toolset for group in shown},
            )
        )
    questions = [spec for spec in specs if spec.toolset is ToolGroup.QUESTIONS]
    if questions:
        capabilities.append(ToolsetCapability(function_toolset(questions, runtime)))
    if enable_prompt_caching:
        capabilities.append(
            PromptCaching(
                conversation_id=ctx.conversation_id,
                protocol=protocol,
                affinity_key=f"{ctx.pod_id}:{ctx.agent_name}" if ctx.pod_id else None,
            )
        )
    return capabilities


def _deferred(
    deferred: Sequence[Sequence[ToolSpec]],
    runtime: ToolRuntime,
    *,
    natively: bool,
    host_execution: bool,
    guided: frozenset[ToolGroup] | set[ToolGroup] = frozenset(),
) -> list[AgentCapability[ToolContext]]:
    """The deferred toolsets, their guidance, and the hint naming them.

    ``natively``: behind pydantic-ai's tool search (provider-native on
    Anthropic/OpenAI, a local `search_tools` elsewhere; see `MeteredModel` for
    the monetary-limit case). Otherwise behind `search_tools` and `call_tool`.
    """
    capabilities: list[AgentCapability[ToolContext]] = []
    # A group whose other tools are in view already carries its guidance.
    guidance = [
        None
        if group[0].toolset in guided
        else toolset_instructions(group[0].toolset, host_execution=host_execution)
        for group in deferred
    ]
    if natively:
        capabilities.append(ToolSearch())
        for group, text in zip(deferred, guidance, strict=True):
            toolset = function_toolset(group, runtime).defer_loading()
            capabilities.append(
                InstructedToolset(toolset, text) if text else ToolsetCapability(toolset)
            )
    else:
        entries = deferred_entry_specs([spec for group in deferred for spec in group])
        capabilities.append(ToolsetCapability(function_toolset(entries, runtime)))
        capabilities.extend(Instructions(text) for text in guidance if text)
    hint = deferred_tools_hint(deferred, by_entry=not natively)
    if hint:
        capabilities.append(Instructions(hint))
    return capabilities


def _run_hooks(
    ctx: ToolContext,
    claim_steered: ClaimSteered | None = None,
    on_steered: OnSteered | None = None,
    steer_due: Callable[[], bool] | None = None,
) -> list[AgentCapability[ToolContext]]:
    """Steering, memory guidance and the platform's guidance, as the run has them."""
    hooks: list[AgentCapability[ToolContext]] = []
    # What the person says while the run works is steered into it.
    if ctx.agent_run_id is not None:
        hooks.append(
            PendingUserMessagesCapability(
                agent_run_id=ctx.agent_run_id,
                claim=claim_steered,
                on_steered=on_steered,
                due=steer_due,
            )
        )
    if ctx.memory_enabled:
        hooks.append(Instructions(load_memory_prompt(after_turn=ctx.memory_after_turn)))
    # The platform is the run's: none for a run asked in Lemma.
    if ctx.surface_platform and platform_is_known(ctx.surface_platform):
        hooks.append(
            Instructions(
                surface_platform_guidance(
                    ctx.surface_platform,
                    audience=run_audience(ctx),
                    in_group=is_group_conversation(ctx),
                )
            )
        )
    return hooks


def _arm(
    groups: list[list[ToolSpec]],
    profile: ToolProfile,
    *,
    defers: bool,
    natively: bool,
) -> tuple[list[list[ToolSpec]], list[list[ToolSpec]]]:
    """The tools in view and behind search, for the run's tool profile."""
    if profile is ToolProfile.DIRECT:
        return _split(groups, defers=False)
    if profile in (ToolProfile.CORE_CLI, ToolProfile.CORE_NATIVE):
        core = [[spec for spec in group if spec.policy.core] for group in groups]
        rest = [[spec for spec in group if not spec.policy.core] for group in groups]
        # Behind the provider's own search, where the array never changes;
        # elsewhere, and on core_cli, the rest is the CLI's.
        keeps_rest = profile is ToolProfile.CORE_NATIVE and natively
        return (
            [group for group in core if group],
            [group for group in rest if group] if keeps_rest else [],
        )
    return _split(groups, defers=defers)


def _split(
    groups: list[list[ToolSpec]], *, defers: bool
) -> tuple[list[list[ToolSpec]], list[list[ToolSpec]]]:
    """Each group's tools in view, and its deferrable ones behind search.

    By tool, not by group: one group can hold a tool every run should see
    beside advanced ones it rarely needs (``delegate`` beside the sub-agent
    controls). A group of only deferrable tools is deferred whole, as before.
    """
    shown: list[list[ToolSpec]] = []
    deferred: list[list[ToolSpec]] = []
    for group in groups:
        hidden = [spec for spec in group if defers and spec.policy.deferrable]
        visible = [spec for spec in group if spec not in hidden]
        if visible:
            shown.append(visible)
        if hidden:
            deferred.append(hidden)
    return shown, deferred


def _groups(specs: Iterable[ToolSpec]) -> list[list[ToolSpec]]:
    """The specs by group, in the order each group first appears."""
    grouped: dict[ToolGroup, list[ToolSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.toolset, []).append(spec)
    return list(grouped.values())


class Instructions(AbstractCapability[ToolContext]):
    """A fixed block of guidance for the cached system-prompt prefix."""

    def __init__(self, text: str) -> None:
        self._text = text

    @classmethod
    def get_serialization_name(cls) -> str | None:  # pragma: no cover - metadata
        return None

    def get_instructions(self) -> str:
        return self._text


class InstructedToolset(Instructions):
    """A toolset and the usage guidance that is part of its contract."""

    def __init__(self, toolset: AbstractToolset[ToolContext], text: str) -> None:
        super().__init__(text)
        self._toolset = toolset

    def get_toolset(self) -> AbstractToolset[ToolContext]:
        return self._toolset


def _call_id(part: ModelResponsePart) -> str | None:
    return part.tool_call_id if isinstance(part, ToolCallPart) else None


def _signature(part: ModelResponsePart) -> str | None:
    assert isinstance(part, ToolCallPart)
    return call_signature(part.tool_name, part.args)


def _raw_signature(part: ModelResponsePart) -> str:
    assert isinstance(part, ToolCallPart)
    return unparsed_signature(part.tool_name, part.args)


def _renamed(part: ModelResponsePart, call_id: str) -> ModelResponsePart:
    assert isinstance(part, ToolCallPart)
    return replace(part, tool_call_id=call_id)


def unique_call_parts(
    parts: list[ModelResponsePart],
) -> tuple[list[ModelResponsePart], list[ModelResponsePart]]:
    """The response's parts with one call per id; and the repeats dropped."""
    return unique_per_call_id(
        parts,
        call_id=_call_id,
        signature=_signature,
        raw_signature=_raw_signature,
        renamed=_renamed,
    )


class UniqueToolCallIds(AbstractCapability[Any]):
    """Run each tool call in a response once, whatever the provider repeated.

    See ``domain/tool_call_ids``: after the response arrives, before its tools
    run. A repeat is dropped; a different call under a taken id is kept under
    an id of its own.
    """

    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        kept, dropped = unique_call_parts(list(response.parts))
        sent_ids = {_call_id(part) for part in response.parts}
        renamed = [
            part
            for part in kept
            if isinstance(part, ToolCallPart) and part.tool_call_id not in sent_ids
        ]
        if not dropped and not renamed:
            return response
        logger.warning(
            "agent.capabilities.duplicate_tool_call_ids.dropped.degraded",
            dropped=len(dropped),
            renamed=len(renamed),
            tool_names=sorted(
                {
                    part.tool_name
                    for part in (*dropped, *renamed)
                    if isinstance(part, ToolCallPart)
                }
            ),
        )
        return replace(response, parts=kept)


class LeaseGate(AbstractCapability[object]):
    """No tool starts on a worker that may no longer hold its turn.

    A tool's effects are not fenced by the turn's lease, so each call first
    asks it (``JobContext.lease_alive``) and, when it may have lapsed, ends the
    turn with ``LeaseLost`` -- left RUNNING for whoever holds it now. A call
    already running is not interrupted here; the heartbeat cancels it.
    """

    def __init__(self, lease_alive: Callable[[], bool]) -> None:
        self._lease_alive = lease_alive

    @classmethod
    def get_serialization_name(cls) -> str | None:  # pragma: no cover - metadata
        return "lease_gate"

    async def wrap_tool_execute(
        self,
        ctx: RunContext[object],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> object:
        if not self._lease_alive():
            logger.warning(
                "agent.turn.tool_skipped_lease_lost",
                tool_name=tool_def.name,
                tool_call_id=call.tool_call_id,
            )
            raise LeaseLost(f"tool {tool_def.name} not started: lease may be lost")
        return await handler(args)


class PromptCaching(AbstractCapability[object]):
    """The caching lever this run's provider protocol understands.

    OpenAI-compatible providers reuse the request prefix on their own; what is
    ours to set is replica affinity, keyed on the pod and agent so a new
    conversation lands where that agent's shared prompt start is cached --
    never the run id, which changes every turn. Anthropic caches nothing
    without breakpoints: after the system prompt, after the tool definitions
    (``search_tools`` changes the tool array mid-run) and on the last message
    block, so a long run reads its own history from cache.
    """

    def __init__(
        self,
        *,
        conversation_id: UUID,
        protocol: RuntimeProfileProtocol = RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        affinity_key: str | None = None,
    ) -> None:
        self._affinity_key = affinity_key or str(conversation_id)
        self._protocol = protocol

    @classmethod
    def get_serialization_name(cls) -> str | None:  # pragma: no cover - metadata
        return "prompt_caching"

    def get_model_settings(self) -> ModelSettings:
        if self._protocol is RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE:
            # Plain per-block `cache_control` rather than the automatic
            # top-level parameter, which some compatible gateways refuse.
            return AnthropicModelSettings(
                anthropic_cache_instructions=_ANTHROPIC_CACHE_TTL,
                anthropic_cache_tool_definitions=_ANTHROPIC_CACHE_TTL,
                anthropic_cache_messages=_ANTHROPIC_CACHE_TTL,
            )
        # The `user` field: the one affinity lever in baseline Chat Completions.
        # `prompt_cache_key` is OpenAI-only and a strict shim rejects it.
        return OpenAIChatModelSettings(openai_user=self._affinity_key)


__all__ = [
    "InstructedToolset",
    "Instructions",
    "LeaseGate",
    "PromptCaching",
    "UniqueToolCallIds",
    "harness_capabilities",
    "unique_call_parts",
]
