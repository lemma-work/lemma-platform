"""A pause emitted alongside `final_answer` must win, and only one thing makes it.

The model can emit `request_approval` and `final_answer` in a single response.
When it does, the pause has to happen: the user is being asked something, and a
run that "completes" instead leaves an approval card that nothing will ever
resolve.

Two mechanisms could plausibly deliver that, and only one of them does. These
tests pin which, because the losing one is the obvious-looking modernisation:

* `end_strategy="graceful"` plus an exception (`AgentInputRequired`) -- what
  LEMMA does. The exception aborts the run outright, so nothing can outrank it.
* pydantic-ai's native `CallDeferred` with `DeferredToolRequests` in the
  `output_type` union -- which reads like the SDK's own answer to this shape and
  is not. `_tool_execution._finalize_deferred` resolves deferred calls only
  `if not self.final_result`, so a validated `final_answer` in the same response
  discards the deferral and the run completes. The pause is silently lost.

These run against a real `FunctionModel`, so they assert the SDK's actual
behaviour rather than our belief about it, and they will fail loudly if a
future pydantic-ai reverses either result.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent, DeferredToolRequests, RunContext
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.output import ToolOutput

from app.modules.agent.tools.tool_errors import AgentInputRequired

PAUSE_ID = "pause-1"
ANSWER_ID = "answer-1"


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
_BOTH_IN_ONE_RESPONSE = (
    ToolCallPart("pausing_tool", {}, tool_call_id=PAUSE_ID),
    ToolCallPart("final_answer", {"output": "done"}, tool_call_id=ANSWER_ID),
)


@pytest.mark.asyncio
async def test_the_pause_wins_when_it_raises_and_the_strategy_is_graceful():
    """LEMMA's arrangement. The exception leaves the run no way to complete."""
    ran: list[str] = []

    async def pausing_tool(ctx: RunContext) -> str:
        ran.append("pausing_tool")
        raise AgentInputRequired(PAUSE_ID, "request_approval")

    agent = Agent(
        _model_emitting(*_BOTH_IN_ONE_RESPONSE),
        output_type=[_FINAL_ANSWER],
        end_strategy="graceful",
        tools=[pausing_tool],
    )

    with pytest.raises(AgentInputRequired) as raised:
        await agent.run("go")

    assert raised.value.tool_call_id == PAUSE_ID
    assert ran == ["pausing_tool"]


@pytest.mark.asyncio
async def test_early_skips_the_pause_entirely():
    """Why `end_strategy` is set at all: under "early" the tool never runs."""
    ran: list[str] = []

    async def pausing_tool(ctx: RunContext) -> str:  # pragma: no cover - never called
        ran.append("pausing_tool")
        raise AgentInputRequired(PAUSE_ID, "request_approval")

    agent = Agent(
        _model_emitting(*_BOTH_IN_ONE_RESPONSE),
        output_type=[_FINAL_ANSWER],
        end_strategy="early",
        tools=[pausing_tool],
    )

    result = await agent.run("go")

    assert result.output == "done"
    assert ran == [], "the pause was skipped, which is the bug graceful prevents"


@pytest.mark.asyncio
async def test_native_deferral_loses_to_final_answer_in_the_same_response():
    """The reason `CallDeferred` is NOT adopted for the in-process dialect.

    The tool runs and raises, `DeferredToolRequests` is in the output union, the
    strategy is graceful -- every condition the native path asks for -- and the
    run still ends on the final answer. The deferral is dropped.
    """
    ran: list[str] = []

    async def pausing_tool(ctx: RunContext) -> str:
        ran.append("pausing_tool")
        raise CallDeferred

    agent = Agent(
        _model_emitting(*_BOTH_IN_ONE_RESPONSE),
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
    """Stated for completeness: the native path works when it is unopposed.

    Which is exactly why the failure above is easy to miss -- every test that
    pauses on its own passes, and only a `final_answer` sibling exposes it.
    """

    async def pausing_tool(ctx: RunContext) -> str:
        raise CallDeferred

    agent = Agent(
        _model_emitting(ToolCallPart("pausing_tool", {}, tool_call_id=PAUSE_ID)),
        output_type=[_FINAL_ANSWER, DeferredToolRequests],
        end_strategy="graceful",
        tools=[pausing_tool],
    )

    result = await agent.run("go")

    assert isinstance(result.output, DeferredToolRequests)
    assert [call.tool_call_id for call in result.output.calls] == [PAUSE_ID]
