"""Unit tests for graceful tool-error handling (GracefulToolset + helpers)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.graceful_toolset import GracefulToolset
from app.modules.agent.tools.tool_errors import (
    format_tool_error,
    is_control_flow_exception,
)


def test_format_tool_error_shape():
    err = format_tool_error("mytool", RuntimeError("nope"))
    assert err == {
        "success": False,
        "error": "nope",
        "error_type": "RuntimeError",
        "tool": "mytool",
    }


def test_is_control_flow_exception():
    assert is_control_flow_exception(ModelRetry("x"))
    assert is_control_flow_exception(asyncio.CancelledError())
    assert is_control_flow_exception(KeyboardInterrupt())
    assert not is_control_flow_exception(RuntimeError("x"))
    assert not is_control_flow_exception(ValueError("x"))


class _RaisingToolset:
    """A minimal toolset stand-in whose call_tool raises a given exception."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def call_tool(self, name, tool_args, ctx, tool):
        raise self._exc


@pytest.mark.anyio
async def test_graceful_toolset_returns_error_on_execution_failure():
    toolset = GracefulToolset(_RaisingToolset(RuntimeError("boom")))
    result = await toolset.call_tool("do_thing", {}, None, None)
    assert result == {
        "success": False,
        "error": "boom",
        "error_type": "RuntimeError",
        "tool": "do_thing",
    }


@pytest.mark.anyio
async def test_graceful_toolset_reraises_model_retry():
    toolset = GracefulToolset(_RaisingToolset(ModelRetry("try again")))
    with pytest.raises(ModelRetry):
        await toolset.call_tool("do_thing", {}, None, None)


@pytest.mark.anyio
async def test_graceful_toolset_reraises_cancellation():
    toolset = GracefulToolset(_RaisingToolset(asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await toolset.call_tool("do_thing", {}, None, None)


@pytest.mark.anyio
async def test_a_cancelled_tool_call_says_which_tool_it_was(caplog):
    """The one fact the cancellation logs did not carry.

    `reraise_driver_failure` already separates who asked for a cancellation --
    its two counters say whether it came from outside or was aimed at the
    driver alone -- but the frames it captures are all harness, the deepest
    being whichever poll loop the task happened to be suspended in. Four
    cancelled runs in dev were diagnosable down to "something inside the graph"
    and no further. This boundary is the only place that knows the tool's name.
    """
    toolset = GracefulToolset(_RaisingToolset(asyncio.CancelledError()))

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await toolset.call_tool("exec_command", {}, None, None)

    said = [r for r in caplog.records if "tool_cancelled_mid_flight" in r.getMessage()]
    assert said, "a cancelled tool call has to name itself"
    assert "exec_command" in said[0].getMessage()


@pytest.mark.anyio
async def test_failing_tool_does_not_abort_a_real_run():
    """A raising tool body becomes a tool response; the run still completes."""

    async def boom() -> str:
        raise RuntimeError("kaboom")

    toolset = GracefulToolset(FunctionToolset(tools=[boom]))

    calls = {"n": 0}

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="boom", args={})])
        return ModelResponse(parts=[TextPart(content="recovered")])

    agent = Agent(FunctionModel(model_fn), toolsets=[toolset], retries=5)
    result = await agent.run("go")

    assert result.output == "recovered"
    # The model saw the error as a tool return rather than the run crashing.
    rendered = "".join(
        str(getattr(part, "content", ""))
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
    )
    assert "kaboom" in rendered
