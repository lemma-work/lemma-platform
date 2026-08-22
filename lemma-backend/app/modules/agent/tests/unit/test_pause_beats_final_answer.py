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
the bug the raise-and-abandon design prevents.

Be precise about the scope, because the obvious summary of this is wrong. It is
not that "an exception is the only thing that outranks output validation". An
output *tool* is what wins here. Text-based output (`NativeOutput`,
`PromptedOutput`) does NOT preempt a deferral, because `_agent_graph`
short-circuits on `if tool_calls:` before consulting the text processor -- so
moving `final_answer` off `ToolOutput` would in fact make native deferral work.
That is a real option and it is still not taken: it would discard the status
lifecycle TASK conversations drive through the `final_answer` tool, it depends
on provider support for structured output, and it does nothing at all for the
Agent Host harness, which is not pydantic-ai.

Also note `requires_approval=True` (`ToolDefinition(kind='unapproved')`) is
worse, not better: the tool body never executes, so there is nowhere to persist
the durable approval id or honour an existing session approval.

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
from pydantic_ai.tools import Tool

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


@pytest.mark.asyncio
async def test_a_declared_approval_tool_loses_too_and_never_even_runs():
    """`requires_approval=True` is the API to reach for next. It is worse.

    `CallDeferred` at least executes the tool body before the deferral is
    discarded. A tool declared `requires_approval=True` is `kind='unapproved'`,
    so the body never runs at all -- which for LEMMA means nowhere to persist
    the durable approval id, and no chance to honour a session approval the
    user already gave. It is stubbed with "a final result was already
    processed" and the run ends on the answer.
    """
    ran: list[str] = []

    async def gated_tool(ctx: RunContext) -> str:  # pragma: no cover - never called
        ran.append("gated_tool")
        return "did it"

    agent = Agent(
        _model_emitting(
            ToolCallPart("gated_tool", {}, tool_call_id=PAUSE_ID),
            ToolCallPart("final_answer", {"output": "done"}, tool_call_id="answer-1"),
        ),
        output_type=[_FINAL_ANSWER, DeferredToolRequests],
        end_strategy="graceful",
        tools=[Tool(gated_tool, requires_approval=True)],
    )

    result = await agent.run("go")

    assert ran == [], "the body never runs, so nothing could persist a pause"
    assert result.output == "done"


@pytest.mark.asyncio
async def test_the_pause_survives_whatever_end_strategy_is_configured():
    """The raise does not depend on the strategy pin.

    `graceful` is pydantic-ai's default now, so the explicit kwarg no longer
    changes behaviour -- it guards against the default moving back, which is
    what it was originally fixing. Under `exhaustive` the pause must hold too.
    """
    from app.modules.agent.tools.tool_errors import AgentInputRequired

    async def pausing_tool(ctx: RunContext) -> str:
        raise AgentInputRequired(PAUSE_ID, "request_approval")

    for strategy in ("graceful", "exhaustive"):
        agent = Agent(
            _model_emitting(
                ToolCallPart("pausing_tool", {}, tool_call_id=PAUSE_ID),
                ToolCallPart(
                    "final_answer", {"output": "done"}, tool_call_id="answer-1"
                ),
            ),
            output_type=[_FINAL_ANSWER],
            end_strategy=strategy,
            tools=[pausing_tool],
        )
        with pytest.raises(AgentInputRequired):
            await agent.run("go")
