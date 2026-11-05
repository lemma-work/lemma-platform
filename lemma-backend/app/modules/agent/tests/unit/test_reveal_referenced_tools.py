"""A later run in a conversation is offered the deferred tools it is told about.

The incident: a run woken with "Read what they said with
`channel_read(asked_by_me=True)`", in a conversation whose history showed
`channel_read` working, started with `channel_read` withheld. Driven here
through pydantic-ai's own `ToolSearch` and a deferred toolset, with a scripted
model that records which tools its first request offered.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.capabilities import Toolset as ToolsetCapability
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.capabilities.reveal_referenced_tools import (
    RevealReferencedToolsCapability,
    referenced_tool_names,
)

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


def _visible() -> FunctionToolset:
    toolset = FunctionToolset()

    @toolset.tool_plain
    def manage_process(action: str) -> list[str]:
        """List background processes."""
        return []

    return toolset


def _earlier_run(*, called: str | None = "channel_read") -> list[ModelMessage]:
    """An earlier run: it searched, then called the tool. Stored and rebuilt,
    the search return is a plain `ToolReturnPart` -- no discovery evidence."""
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("Ask the cofounders.")]),
        ModelResponse(
            parts=[ToolCallPart("search_tools", {"queries": ["message"]}, "s1")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "search_tools",
                    {"discovered_tools": [{"name": "channel_post"}]},
                    "s1",
                )
            ]
        ),
    ]
    if called:
        history += [
            ModelResponse(parts=[ToolCallPart(called, {}, "c1")]),
            ModelRequest(parts=[ToolReturnPart(called, "ok", "c1")]),
        ]
    history.append(ModelResponse(parts=[TextPart("Asked them.")]))
    return history


async def _offered_first(
    *, reveal: bool, history: list[ModelMessage], prompt: str
) -> set[str]:
    offered: list[set[str]] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append({tool.name for tool in info.function_tools})
        return ModelResponse(parts=[TextPart("done")])

    capabilities = [
        ToolsetCapability(_visible()),
        ToolSearch(),
        ToolsetCapability(_messaging().defer_loading()),
    ]
    if reveal:
        capabilities.append(RevealReferencedToolsCapability())
    agent = Agent(FunctionModel(respond), capabilities=capabilities)
    await agent.run(prompt, message_history=history)
    return offered[0]


async def test_without_it_the_woken_run_is_not_offered_the_tool_it_is_told_to_call():
    """The incident, reproduced: the control this capability exists for."""
    offered = await _offered_first(reveal=False, history=_earlier_run(), prompt=WAKE)

    assert "channel_read" not in offered


async def test_a_woken_run_is_offered_the_tool_its_wake_up_and_history_name():
    offered = await _offered_first(reveal=True, history=_earlier_run(), prompt=WAKE)

    assert {"channel_read", "channel_post"} <= offered


async def test_a_tool_named_nowhere_stays_deferred():
    """Only what the run is told about: deferral is otherwise untouched."""
    offered = await _offered_first(
        reveal=True, history=_earlier_run(called=None), prompt="What's on today?"
    )

    assert "channel_read" not in offered
    assert "channel_post" in offered  # found by the earlier run's search


def test_what_counts_as_referenced():
    names = referenced_tool_names(
        [*_earlier_run(), ModelRequest(parts=[UserPromptPart(WAKE)])]
    )

    assert {"channel_post", "channel_read", "search_tools"} <= names
    assert "Ask" not in names


async def test_instructions_naming_a_deferred_tool_do_not_reveal_it():
    """A deferred toolset's own guidance names its tools on every run; reading
    it would undo the deferral for the pod assistant."""
    offered: list[set[str]] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append({tool.name for tool in info.function_tools})
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(respond),
        instructions="Read answers with channel_read(asked_by_me=True).",
        capabilities=[
            ToolSearch(),
            ToolsetCapability(_messaging().defer_loading()),
            RevealReferencedToolsCapability(),
        ],
    )
    await agent.run("What's on today?")

    assert "channel_read" not in offered[0]


async def test_the_prompt_stays_the_last_thing_the_model_reads():
    """The reveal goes in before the wake-up, not after it."""
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(respond),
        capabilities=[
            ToolSearch(),
            ToolsetCapability(_messaging().defer_loading()),
            RevealReferencedToolsCapability(),
        ],
    )
    await agent.run(WAKE, message_history=_earlier_run())

    last = seen[0][-1]
    assert isinstance(last, ModelRequest)
    assert any(
        isinstance(part, UserPromptPart) and part.content == WAKE for part in last.parts
    )


def test_what_a_compaction_replaced_is_not_referenced():
    """pydantic-ai resets discovery at a compaction; so does this."""
    from pydantic_ai.messages import CompactionPart

    compacted = ModelResponse(parts=[CompactionPart(content="summary")])

    names = referenced_tool_names([*_earlier_run(), compacted])

    assert "channel_read" not in names
