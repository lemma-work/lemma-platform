"""A later run is offered the deferred tools an earlier run found.

The incident: a run woken with "Read what they said with
`channel_read(asked_by_me=True)`", in a conversation whose history showed
`channel_read` working, started with `channel_read` withheld -- the stored
`search_tools` return had been rebuilt from message rows as a plain return,
which pydantic-ai does not read as a discovery. With no text answer allowed the
run called the next tool in its list, 1,399 times.

The run log replays the search exactly as it happened, so the discovery carries
over by itself; nothing has to re-reveal it. Driven through pydantic-ai's own
`ToolSearch` and a deferred toolset, the earlier run's messages round-tripped
through the log's serialization.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.capabilities import Toolset as ToolsetCapability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.infrastructure.harness.lemma.run_log import decode, encode

pytestmark = pytest.mark.unit

WAKE = "Everyone answered. Read what they said with channel_read(asked_by_me=True), then carry on."


def _messaging() -> FunctionToolset:
    toolset = FunctionToolset()

    @toolset.tool_plain
    def channel_read(asked_by_me: bool = False) -> str:
        """Read the answers people sent."""
        return "Priya: yes"

    @toolset.tool_plain
    def channel_post(text: str) -> str:
        """Ask somebody something."""
        return "sent"

    return toolset


def _agent(respond) -> Agent:  # noqa: ANN001
    return Agent(
        FunctionModel(respond),
        capabilities=[ToolSearch(), ToolsetCapability(_messaging().defer_loading())],
    )


async def _earlier_run() -> list[ModelMessage]:
    """It searched, found the messaging tools, and read the answers."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = sum(isinstance(message, ModelResponse) for message in messages)
        if step == 0:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_tools", {"queries": ["read answers"]}, "search-1"
                    )
                ]
            )
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("channel_read", {}, "read-1")])
        return ModelResponse(parts=[TextPart("Asked them.")])

    result = await _agent(respond).run("Ask the cofounders.")
    return result.all_messages()


async def _offered_first(history: list[ModelMessage]) -> set[str]:
    offered: list[set[str]] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append({tool.name for tool in info.function_tools})
        return ModelResponse(parts=[TextPart("done")])

    await _agent(respond).run(WAKE, message_history=history)
    return offered[0]


async def test_a_woken_run_replaying_the_log_is_offered_what_it_found() -> None:
    earlier = await _earlier_run()

    offered = await _offered_first(decode([encode(earlier)]))

    assert "channel_read" in offered


async def test_a_tool_nobody_searched_for_stays_deferred() -> None:
    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Nothing to do.")])

    earlier = (await _agent(respond).run("Hello.")).all_messages()

    assert "channel_read" not in await _offered_first(decode([encode(earlier)]))
