"""What pydantic-ai is handed for a run beyond its tools.

The usage limits it enforces, the final-answer output a task owes, and whether
the provider's prompt caching is asked for -- each decided from Lemma's own
types, so no service holds a pydantic-ai one.
"""

from __future__ import annotations

from pydantic_ai import UsageLimits
from pydantic_ai.output import OutputSpec, ToolOutput

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.modules.agent.domain.value_objects import ConversationType
from app.modules.agent.infrastructure.harness.lemma.final_answer_output import (
    get_final_answer_tool,
)


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


def prompt_caching_enabled(snapshot: RuntimeSnapshot | None) -> bool:
    """Whether to ask for caching: both protocols cache, by different levers."""
    return (
        snapshot is not None
        and snapshot.protocol
        in (
            RuntimeProfileProtocol.OPENAI_COMPATIBLE,
            RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
        )
        and agent_settings.lemma_llm_caching_enabled
    )


__all__ = ["final_answer_output", "harness_usage_limits", "prompt_caching_enabled"]
