"""Unit tests for AgentToolDispatcher resolve + invoke mechanics."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher, UnknownToolError


async def echo_tool(ctx: RunContext[ToolContext], value: int) -> dict:
    """Echo a value back along with the pod id from context."""
    return {"value": value, "pod_id": str(ctx.deps.pod_id)}


_TOOLSET = FunctionToolset[ToolContext](tools=[echo_tool])


def _ctx() -> ToolContext:
    return ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())


@pytest.mark.asyncio
async def test_lists_tools_from_explicit_toolsets():
    dispatcher = AgentToolDispatcher(uow_factory=object())

    tools = await dispatcher.list_tools(ctx=_ctx(), toolsets=[_TOOLSET])

    by_name = {tool.name: tool for tool in tools}
    assert "echo_tool" in by_name
    assert by_name["echo_tool"].description.startswith("Echo a value")


@pytest.mark.asyncio
async def test_calls_tool_with_run_context_deps():
    ctx = _ctx()
    dispatcher = AgentToolDispatcher(uow_factory=object())

    result = await dispatcher.call_tool(
        ctx=ctx,
        toolsets=[_TOOLSET],
        name="echo_tool",
        arguments={"value": 7},
    )

    assert result == {"value": 7, "pod_id": str(ctx.pod_id)}


@pytest.mark.asyncio
async def test_unknown_tool_raises():
    dispatcher = AgentToolDispatcher(uow_factory=object())

    with pytest.raises(UnknownToolError):
        await dispatcher.call_tool(
            ctx=_ctx(),
            toolsets=[_TOOLSET],
            name="does_not_exist",
            arguments={},
        )


class _RecordingToolset(FunctionToolset[ToolContext]):
    """Records being opened, which is the cost a call should not pay twice."""

    def __init__(self, *args, opened: list[str], label: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._opened = opened
        self._label = label

    async def __aenter__(self):
        self._opened.append(self._label)
        return await super().__aenter__()


async def other_tool(ctx: RunContext[ToolContext]) -> str:
    """A tool nobody calls here."""
    return "unused"


@pytest.mark.asyncio
async def test_a_call_opens_only_the_toolsets_up_to_the_one_that_owns_it():
    """Every MCP `tools/call` used to enter every toolset of the run to invoke
    one tool. The walk now stops at the owner."""
    opened: list[str] = []
    first = _RecordingToolset(tools=[echo_tool], opened=opened, label="first")
    second = _RecordingToolset(tools=[other_tool], opened=opened, label="second")
    dispatcher = AgentToolDispatcher(uow_factory=object())

    await dispatcher.call_tool(
        ctx=_ctx(), toolsets=[first, second], name="echo_tool", arguments={"value": 1}
    )

    assert opened == ["first"]


@pytest.mark.asyncio
async def test_listing_still_opens_every_toolset():
    opened: list[str] = []
    first = _RecordingToolset(tools=[echo_tool], opened=opened, label="first")
    second = _RecordingToolset(tools=[other_tool], opened=opened, label="second")
    dispatcher = AgentToolDispatcher(uow_factory=object())

    tools = await dispatcher.list_tools(ctx=_ctx(), toolsets=[first, second])

    assert opened == ["first", "second"]
    assert {tool.name for tool in tools} == {"echo_tool", "other_tool"}
