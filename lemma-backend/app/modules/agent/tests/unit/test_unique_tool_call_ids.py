"""A provider that repeats a tool call under one id gets it run once.

Fireworks returned `manage_process` twice under one call id. Left alone,
pydantic-ai fails the whole run on such a response ("tool calls must have
unique tool_call_id values"). Here a scripted model does the same through a
real pydantic-ai run, which now carries on.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import Toolset as ToolsetCapability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.capabilities.unique_tool_call_ids import (
    UniqueToolCallIdsCapability,
)
from app.modules.agent.domain.tool_call_ids import first_per_call_id

pytestmark = pytest.mark.unit


async def test_a_call_repeated_under_one_id_runs_once():
    ran: list[str] = []
    toolset = FunctionToolset()

    @toolset.tool_plain
    def send(text: str) -> str:
        """Send a message."""
        ran.append(text)
        return "sent"

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if len(messages) > 1:
            return ModelResponse(parts=[TextPart("done")])
        call = ToolCallPart("send", {"text": "hello"}, "same-id")
        return ModelResponse(
            parts=[call, ToolCallPart("send", {"text": "hello"}, "same-id")]
        )

    agent = Agent(
        FunctionModel(respond),
        capabilities=[ToolsetCapability(toolset), UniqueToolCallIdsCapability()],
    )
    await agent.run("say hello")

    assert ran == ["hello"]


async def test_two_different_calls_under_one_id_both_run_and_pair_with_their_results():
    """Dropping the second lost a call the model asked for, and the result it
    was owed paired with nothing."""
    ran: list[str] = []
    toolset = FunctionToolset()

    @toolset.tool_plain
    def send(text: str) -> str:
        """Send a message."""
        ran.append(text)
        return f"sent {text}"

    returned: dict[str, object] = {}

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if len(messages) > 1:
            for part in messages[-1].parts:
                returned[part.tool_call_id] = part.content
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart("send", {"text": "hello"}, "call_0"),
                ToolCallPart("send", '{"text": "bye"}', "call_0"),
            ]
        )

    agent = Agent(
        FunctionModel(respond),
        capabilities=[ToolsetCapability(toolset), UniqueToolCallIdsCapability()],
    )
    await agent.run("say both")

    assert sorted(ran) == ["bye", "hello"]
    assert returned["call_0"] == "sent hello"
    [other] = [key for key in returned if key != "call_0"]
    assert other.startswith("call_0-") and returned[other] == "sent bye"


def test_the_stored_calls_get_the_ids_the_executed_calls_ran_under():
    """The two halves see the same calls but one: a call whose arguments never
    parsed is executed, not stored. It cannot move a stored call's id."""
    from app.modules.agent.capabilities.unique_tool_call_ids import unique_call_parts
    from app.modules.agent.domain.value_objects import (
        AgentEvent,
        AgentEventType,
        MessageDraft,
    )
    from app.modules.agent.infrastructure.harnesses.pydantic_ai_node_loop import (
        unique_stored_calls,
    )

    executed, _ = unique_call_parts(
        [
            ToolCallPart("send", "{not json", "call_0"),
            ToolCallPart("send", {"text": "a"}, "call_0"),
            ToolCallPart("send", {"text": "b"}, "call_0"),
            ToolCallPart("send", {"text": "a"}, "call_0"),
        ]
    )
    stored = unique_stored_calls(
        [
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_call(
                    tool_name="send", tool_call_id="call_0", tool_args={"text": text}
                ),
            )
            for text in ("a", "b", "a")
        ]
    )

    executed_ids = [part.tool_call_id for part in executed]
    stored_ids = [event.data.tool_call_id for event in stored]
    assert len(executed_ids) == len(set(executed_ids)) == 3
    # The unparsed call keeps its place but gives up the id it shared.
    assert executed_ids[0] != "call_0"
    assert stored_ids == executed_ids[1:] == ["call_0", stored_ids[1]]


def test_the_first_call_per_id_stands_and_untagged_items_stay():
    items = [("text", None), ("a", "1"), ("b", "1"), ("c", "2")]

    kept, dropped = first_per_call_id(items, lambda item: item[1])

    assert kept == [("text", None), ("a", "1"), ("c", "2")]
    assert dropped == [("b", "1")]
