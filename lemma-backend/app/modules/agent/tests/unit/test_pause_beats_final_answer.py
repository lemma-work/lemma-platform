"""Why the pause is a raised exception and not pydantic-ai's native deferral.

That a pause emitted alongside `final_answer` must still pause is already
pinned, over both pausing tools, by `TestPausingToolsBesideFinalAnswer` in
`test_pydantic_ai_harness_stop.py` -- including the "early" leg that reproduces
the original production bug. This file covers the question that comes *next*,
and keeps coming up: the SDK now has a native pause (`CallDeferred` plus
`DeferredToolRequests` in the output union), so could that replace the exception
and let `end_strategy="graceful"` go?

It cannot, and the reason is one line of the SDK:

    _tool_execution.py  ::  _finalize_deferred
    if not self.final_result and self.deferred_calls:

Deferred calls are resolved into the run's output only when nothing else has
already produced a final result. A validated `final_answer` in the same response
sets one, so the deferral is discarded and the run completes -- which is exactly
the bug `graceful` exists to prevent. An exception outranks output validation
because it abandons the run; an output value competes with the other output
values, and loses.

The second test is here because it is what makes the first easy to miss: the
native path works perfectly when nothing outranks it, so every test that pauses
in isolation passes.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.output import ToolOutput

PAUSE_ID = "pause-1"


def _model_emitting(*parts: ToolCallPart) -> FunctionModel:
    """A model that emits these calls once, then plain text."""

    def reply(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=list(parts))
        return ModelResponse(parts=[TextPart("continued")])

    return FunctionModel(reply)


async def _final_answer(ctx: RunContext, output: str) -> str:
    return output


_FINAL_ANSWER = ToolOutput(_final_answer, name="final_answer")


async def _deferring_tool(ctx: RunContext) -> str:
    raise CallDeferred


@pytest.mark.asyncio
async def test_native_deferral_loses_to_final_answer_in_the_same_response():
    """Every condition the native path asks for, and the pause is still dropped."""
    ran: list[str] = []

    async def pausing_tool(ctx: RunContext) -> str:
        ran.append("pausing_tool")
        raise CallDeferred

    agent = Agent(
        _model_emitting(
            ToolCallPart("pausing_tool", {}, tool_call_id=PAUSE_ID),
            ToolCallPart("final_answer", {"output": "done"}, tool_call_id="answer-1"),
        ),
        output_type=[_FINAL_ANSWER, DeferredToolRequests],
        end_strategy="graceful",
        tools=[pausing_tool],
    )

    result = await agent.run("go")

    assert ran == ["pausing_tool"], "the tool did run and did raise CallDeferred"
    assert not isinstance(result.output, DeferredToolRequests)
    assert result.output == "done", "the final answer outranked the deferral"


@pytest.mark.asyncio
async def test_native_deferral_does_pause_when_nothing_outranks_it():
    """Unopposed, the native path is fine -- which is why the above is easy to miss."""
    agent = Agent(
        _model_emitting(ToolCallPart("_deferring_tool", {}, tool_call_id=PAUSE_ID)),
        output_type=[_FINAL_ANSWER, DeferredToolRequests],
        end_strategy="graceful",
        tools=[_deferring_tool],
    )

    result = await agent.run("go")

    assert isinstance(result.output, DeferredToolRequests)
    assert [call.tool_call_id for call in result.output.calls] == [PAUSE_ID]
