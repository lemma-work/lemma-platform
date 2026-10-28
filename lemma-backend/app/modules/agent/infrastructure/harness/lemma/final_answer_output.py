"""The in-process harness's final answer: an output tool, not a ToolSpec.

pydantic-ai ends the run when the model calls it. The Agent Host's
``final_answer`` is a real tool instead (``tools/final_answer``), because a
remote agent has no output channel; both settle into ``FinalAgentResult``.
"""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.output import StructuredDict

from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.value_objects import to_json_value
from app.modules.agent.infrastructure.tools.final_answer.tools import FinalAgentResult
from app.modules.agent.domain.context import ToolContext


def get_final_answer_tool(
    agent: Agent,
) -> Callable:
    """Factory that returns final_answer callable based on agent.output_schema.

    Args:
        agent: Agent entity with optional output_schema

    Returns:
        Async callable for final_answer tool
    """
    output_type = StructuredDict(agent.output_schema) if agent.output_schema else str

    async def final_answer(
        ctx: RunContext[ToolContext],
        status: Literal["COMPLETED", "FAILED", "WAITING"],
        output: output_type,
        error: str | None = None,
    ) -> FinalAgentResult:
        """Return normalized final agent data for adapter-side handling."""
        _ = ctx

        output_data = None
        if output is not None:
            if isinstance(output, BaseModel):
                output_data = output.model_dump()
            else:
                output_data = to_json_value(output)

        if status == "FAILED" and not error:
            error = str(output_data) if output_data else "Agent run failed"

        return FinalAgentResult(
            status=status,
            output=output_data,
            error=error,
        )

    return final_answer


__all__ = ["get_final_answer_tool", "FinalAgentResult"]
