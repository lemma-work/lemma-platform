"""A run's system instructions, laid out by how often each part changes.

A provider caches the request prefix, so the most stable text leads:

1. the deployment's own prompt (the pod, the base prompt, reply discipline);
2. each capability's guidance -- large, and the same for every conversation
   with these toolsets;
3. the agent's own instruction: the same for every conversation with this
   agent, and the end of what a new conversation reuses;
4. the conversation's context (working directory, conversation instructions,
   the runtime brief with the pod's inventory), frozen where the conversation
   started.

PydanticAI puts every literal instruction first and instruction functions
after the capabilities' guidance, which is why (3) and (4) are functions.

(4) is an ``on_change='append'`` block: pydantic-ai records its value in the
history (an ``InstructionBaselinePart``, which the run log carries) and keeps
sending that value in the prefix; when it changes -- a table created, a file
added -- the new value is appended to the history as an ``InstructionDeltaPart``
after everything already sent, instead of rewriting the prompt in front of the
whole conversation. A compaction starts a new baseline.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic_ai.messages import InstructionPart

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.prompt.templates import (
    agent_instruction_section,
    build_agent_instruction_parts,
)
from app.modules.agent.services.run_phase_spans import run_phase

#: The name the conversation's context block is addressed by in the history.
CONVERSATION_CONTEXT = "conversation"


def run_instructions(
    *,
    agent: Agent,
    conversation: Conversation,
    ctx: ToolContext,
) -> list[str | Callable[[], str | InstructionPart]]:
    """The run's system instructions, timed as their own phase."""
    with run_phase("instructions") as span:
        stable, per_conversation = build_agent_instruction_parts(
            agent=agent,
            conversation=conversation,
            ctx=ctx,
            include_toolset_prompts=False,
            include_agent_instruction=False,
        )
        own = agent_instruction_section(agent)
        span.set_attribute(
            "lemma.instructions.chars", len(stable) + len(own) + len(per_conversation)
        )

        def this_agent() -> str:
            return own

        def this_conversation() -> InstructionPart:
            return InstructionPart(
                content=per_conversation,
                name=CONVERSATION_CONTEXT,
                on_change="append",
            )

        return [stable, this_agent, this_conversation]


__all__ = ["CONVERSATION_CONTEXT", "run_instructions"]
