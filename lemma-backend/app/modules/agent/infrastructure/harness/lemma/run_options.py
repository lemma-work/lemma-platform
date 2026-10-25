"""The pydantic-ai parts of what a run hands its harness.

The runner (``services/agent_runner_service``) decides what a run gets in
Lemma's terms; the framework objects it becomes are built here, so no service
holds a pydantic-ai type: the usage limits pydantic-ai enforces, the
final-answer output a task owes, and the tool surface each harness reads.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import UsageLimits
from pydantic_ai.capabilities import AgentCapability
from pydantic_ai.output import OutputSpec, ToolOutput
from pydantic_ai.toolsets import AbstractToolset

from app.modules.agent.capabilities import build_lemma_harness_tooling
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.value_objects import ConversationType, HarnessKind
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.final_answer import get_final_answer_tool

type Toolsets = list[AbstractToolset[ConversationContext]]
type Capabilities = list[AgentCapability[ConversationContext]]


def harness_usage_limits() -> UsageLimits:
    """The limits pydantic-ai enforces inside one run: none on request count.

    `RunSpend` is the run's request budget. It warns the run as it nears the
    end and pauses to ask a person whether to carry on, which is what the run's
    own instructions promise. A second, lower count inside pydantic-ai ended the
    run instead -- before the warning, before the pause -- with an error telling
    the reader to check model settings that were never the problem.

    Explicitly `None` rather than omitted: pydantic-ai substitutes its own
    default of 50 requests when it is handed no limits at all.
    """
    return UsageLimits(request_limit=None)


def final_answer_output(
    agent: Agent, conversation: Conversation
) -> OutputSpec[object] | None:
    """The final-answer tool a task conversation owes, else nothing.

    TASK conversations always get it: it drives the task lifecycle (status
    WAITING/COMPLETED/FAILED), not just structured output. The output *schema*
    is only applied when the agent configures one (``get_final_answer_tool``).

    A tool, named rather than left to the model's default: a model that thinks
    by default would otherwise be asked for native JSON-schema output, which
    some Anthropic-compatible gateways refuse.
    """
    if conversation.type == ConversationType.TASK:
        return ToolOutput(get_final_answer_tool(agent))
    return None


async def harness_tooling(
    *,
    ctx: ConversationContext,
    full_toolsets: Sequence[AbstractToolset[ConversationContext]],
    resolved: ResolvedAgentRuntime,
) -> tuple[Toolsets, Capabilities]:
    """The toolsets and capabilities a run's harness reads.

    Remote harnesses reach every tool through the MCP server and keep the full
    list. The in-process harness realizes every tool surface as a capability --
    core tools shown directly, the rest deferred, current time, caching and the
    todo layered on -- so its toolset list is empty.
    """
    if resolved.harness_kind != HarnessKind.LEMMA:
        return list(full_toolsets), []
    capabilities = await build_lemma_harness_tooling(
        ctx=ctx,
        full_toolsets=list(full_toolsets),
        # Both protocols cache, by different mechanisms -- see
        # PromptCachingCapability.
        enable_prompt_caching=(
            resolved.profile.protocol
            in (
                RuntimeProfileProtocol.OPENAI_COMPATIBLE,
                RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
            )
            and agent_settings.lemma_llm_caching_enabled
        ),
        protocol=resolved.profile.protocol,
    )
    return [], capabilities


__all__ = ["final_answer_output", "harness_tooling", "harness_usage_limits"]
