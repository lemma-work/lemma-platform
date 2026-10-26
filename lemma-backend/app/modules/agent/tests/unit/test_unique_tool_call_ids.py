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

from app.modules.agent.infrastructure.harness.lemma.capabilities import (
    UniqueToolCallIds as UniqueToolCallIdsCapability,
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


def test_the_first_call_per_id_stands_and_untagged_items_stay():
    items = [("text", None), ("a", "1"), ("b", "1"), ("c", "2")]

    kept, dropped = first_per_call_id(items, lambda item: item[1])

    assert kept == [("text", None), ("a", "1"), ("c", "2")]
    assert dropped == [("b", "1")]
