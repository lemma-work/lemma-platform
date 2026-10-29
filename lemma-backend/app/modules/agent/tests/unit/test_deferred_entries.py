"""``search_tools`` and ``call_tool``: the deferred tools behind two fixed entries."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import BaseModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import ToolGroup, ToolPolicy, tool
from app.modules.agent.infrastructure.harness.lemma.harness import (
    reveals_tools_natively,
)
from app.modules.agent.infrastructure.tools.deferred.tools import (
    as_called,
    deferred_entry_specs,
)
from app.modules.agent.services.tool_runtime import ToolRuntime

pytestmark = pytest.mark.unit


class Booking(BaseModel):
    hotel: str
    nights: int


async def book_hotel(ctx: ToolContext, request: Booking) -> dict[str, object]:
    """Book a hotel room for some nights."""
    return {"booked": request.hotel, "call": ctx.tool_call_id}


class Query(BaseModel):
    city: str


async def find_flights(ctx: ToolContext, request: Query) -> str:
    """Find flights to a city."""
    return request.city


_DEFERRED = [
    tool(book_hotel, ToolGroup.SUBAGENTS, ToolPolicy(deferrable=True)),
    tool(find_flights, ToolGroup.SUBAGENTS, ToolPolicy(deferrable=True)),
]


def _ctx() -> ToolContext:
    return ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())


async def _call(name: str, arguments: dict[str, object]) -> object:
    entries = deferred_entry_specs(_DEFERRED)
    runtime = ToolRuntime([*_DEFERRED, *entries])
    return await runtime.call(name, arguments, _ctx(), tool_call_id="call_1")


@pytest.mark.anyio
async def test_a_search_returns_the_tools_with_their_parameters() -> None:
    found = await _call("search_tools", {"queries": ["book a hotel"]})

    assert isinstance(found, dict)
    (hit,) = found["tools"]
    assert hit["name"] == "book_hotel"
    assert set(hit["parameters"]["properties"]) == {"hotel", "nights"}


@pytest.mark.anyio
async def test_call_tool_runs_the_named_tool_under_the_calls_id() -> None:
    result = await _call(
        "call_tool",
        {"name": "book_hotel", "arguments": {"hotel": "Grand", "nights": 2}},
    )

    assert result == {"booked": "Grand", "call": "call_1"}


@pytest.mark.anyio
async def test_bad_arguments_and_unknown_names_are_said_back() -> None:
    invalid = await _call("call_tool", {"name": "book_hotel", "arguments": {}})
    unknown = await _call("call_tool", {"name": "exec_command", "arguments": {}})

    assert isinstance(invalid, dict) and invalid["success"] is False
    assert "Invalid arguments" in str(invalid["error"])
    assert isinstance(unknown, dict) and "search_tools" in str(unknown["error"])


def test_a_call_through_call_tool_reads_as_the_tool_it_ran() -> None:
    assert as_called("call_tool", {"name": "book_hotel", "arguments": {"n": 1}}) == (
        "book_hotel",
        {"n": 1},
    )
    assert as_called("book_hotel", {"name": "x"}) is None
    assert as_called("call_tool", {"arguments": {}}) is None


def test_which_models_reveal_a_tool_without_rewriting_the_array() -> None:
    chat = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="unused"))
    anthropic = AnthropicModel(
        "claude-sonnet-4-5", provider=AnthropicProvider(api_key="unused")
    )

    assert reveals_tools_natively(anthropic)
    assert not reveals_tools_natively(chat)
    assert not reveals_tools_natively(FunctionModel(lambda *_: None))  # type: ignore[arg-type,return-value]
