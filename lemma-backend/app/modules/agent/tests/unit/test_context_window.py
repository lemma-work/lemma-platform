"""A request -- instructions, tools, history and reply -- fits the model's window.

The compaction trigger and the hard ceiling measured the message history alone,
while the instructions and tool schemas rode in the same window and the reply
budget took whatever the ceiling left. A history held at the ceiling therefore
overran the window by construction: the provider rejection the ceiling exists to
prevent. These drive real pydantic-ai requests through the same capabilities a
run uses and measure what reached the model.
"""

from __future__ import annotations

import random

import pytest
from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import Tool

from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.infrastructure.harness.lemma.history import (
    drop_orphan_tool_results,
    enforce_token_ceiling,
    find_safe_cutoff,
    history_capabilities,
)
from app.modules.agent.services.context_budget import context_budget_for
from app.modules.agent.infrastructure.providers.tokens import (
    MessageTokenCounter,
    count_model_message_tokens,
    count_text_tokens,
    count_tool_definition_tokens,
)

pytestmark = pytest.mark.unit

_WINDOW = 40_000


def _budget():
    class _Model:
        metadata = {"context_window": _WINDOW}

    return context_budget_for(_Model())


def _tools(count: int) -> list[Tool[None]]:
    """Tools with schemas the size of real ones: several described fields each."""

    def make(index: int) -> Tool[None]:
        def run(
            query: str,
            limit: int = 10,
            offset: int = 0,
            include_archived: bool = False,
            sort_by: str = "created_at",
        ) -> str:
            """Search the workspace's records and return the matching rows.

            Args:
                query: Free-text search across every indexed column of the table.
                limit: How many rows to return at most, between one and a thousand.
                offset: How many matching rows to skip, for reading a long result.
                include_archived: Whether archived rows are searched as well.
                sort_by: The column to order by, newest first.
            """
            return "ok"

        return Tool(run, name=f"search_records_{index}")

    return [make(index) for index in range(count)]


def _history_of(tokens: int) -> list[ModelMessage]:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="Reconcile the ledger.")])
    ]
    index = 0
    while count_model_message_tokens(messages) < tokens:
        call_id = f"c{index}"
        messages.append(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search_records_0", args={}, tool_call_id=call_id
                    )
                ]
            )
        )
        messages.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="search_records_0",
                        content="row " * 200,
                        tool_call_id=call_id,
                    )
                ]
            )
        )
        index += 1
    messages.append(ModelResponse(parts=[TextPart("Working on it.")]))
    return messages


async def test_instructions_tools_history_and_reply_fit_inside_the_window() -> None:
    budget = _budget()
    instructions = "Keep the books balanced and say what changed. " * 900
    assert count_text_tokens(instructions) >= 8_000
    tools = _tools(20)
    history = _history_of(int(_WINDOW * 0.91))
    totals: list[int] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_tokens = sum(
            count_tool_definition_tokens(
                tool.name, tool.description, tool.parameters_json_schema
            )
            for tool in info.function_tools
        )
        totals.append(
            count_text_tokens(info.instructions or "")
            + tool_tokens
            + count_model_message_tokens(messages)
            + int((info.model_settings or {})["max_tokens"])
        )
        return ModelResponse(parts=[TextPart("Balanced.")])

    options = HarnessOptions(
        model_name="test-model",
        history_summarization_enabled=False,
        history_hard_token_ceiling=budget.hard_token_ceiling,
        context_window=budget.window,
    )
    agent = PydanticAIAgent(
        FunctionModel(answer),
        instructions=instructions,
        tools=tools,
        model_settings={"max_tokens": budget.reply_token_budget},
        capabilities=history_capabilities(options, summarization_model="unused"),
    )

    await agent.run("And the totals?", message_history=history)

    (total,) = totals
    assert total <= _WINDOW


async def test_the_reply_budget_is_never_raised_or_invented() -> None:
    """A model's own output limit can be smaller than its window."""
    budget = _budget()
    seen: list[object] = []

    def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        seen.append((info.model_settings or {}).get("max_tokens"))
        return ModelResponse(parts=[TextPart("ok")])

    options = HarnessOptions(
        model_name="test-model",
        history_summarization_enabled=False,
        history_hard_token_ceiling=budget.hard_token_ceiling,
        context_window=budget.window,
    )
    capped = PydanticAIAgent(
        FunctionModel(answer),
        model_settings={"max_tokens": 512},
        capabilities=history_capabilities(options, summarization_model="unused"),
    )
    uncapped = PydanticAIAgent(
        FunctionModel(answer),
        capabilities=history_capabilities(options, summarization_model="unused"),
    )

    await capped.run("hi")
    await uncapped.run("hi")

    assert seen == [512, None]


def test_a_retry_prompt_for_a_tool_is_not_cut_away_from_its_call() -> None:
    """pydantic-ai answers invalid tool arguments with a `RetryPromptPart`
    naming the tool; a provider reads it as a tool message."""
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c1")]),
        ModelRequest(
            parts=[
                RetryPromptPart(content="bad args", tool_name="t", tool_call_id="c1")
            ]
        ),
        ModelResponse(parts=[TextPart("done")]),
    ]

    assert find_safe_cutoff(messages, 2) == 3


def test_a_tool_result_whose_call_was_cut_is_dropped() -> None:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="go")]),
        ModelRequest(
            parts=[
                RetryPromptPart(content="bad args", tool_name="t", tool_call_id="gone")
            ]
        ),
        ModelResponse(parts=[TextPart("done")]),
        ModelRequest(parts=[UserPromptPart(content="next")]),
    ]

    kept = drop_orphan_tool_results(messages)

    assert [type(m).__name__ for m in kept] == [
        "ModelRequest",
        "ModelResponse",
        "ModelRequest",
    ]


def _random_history(rng: random.Random) -> list[ModelMessage]:
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="task")])
    ]
    for step in range(rng.randint(3, 40)):
        roll = rng.random()
        if roll < 0.15:
            messages.append(
                ModelRequest(
                    parts=[UserPromptPart(content="more " * rng.randint(1, 80))]
                )
            )
            continue
        calls = [f"s{step}c{n}" for n in range(rng.randint(1, 3))]
        messages.append(
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="t", args={}, tool_call_id=c) for c in calls
                ]
            )
        )
        results: list[object] = []
        for call in calls:
            if rng.random() < 0.3:
                results.append(
                    RetryPromptPart(content="invalid", tool_name="t", tool_call_id=call)
                )
            else:
                results.append(
                    ToolReturnPart(
                        tool_name="t",
                        content="out " * rng.randint(1, 300),
                        tool_call_id=call,
                    )
                )
        messages.append(ModelRequest(parts=results))  # type: ignore[arg-type]
    return messages


def _orphans(messages: list[ModelMessage]) -> list[str]:
    seen: set[str] = set()
    orphaned: list[str] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                seen.add(part.tool_call_id)
            is_result = isinstance(part, ToolReturnPart) or (
                isinstance(part, RetryPromptPart) and part.tool_name
            )
            if is_result and part.tool_call_id not in seen:
                orphaned.append(part.tool_call_id)
    return orphaned


@pytest.mark.parametrize("seed", range(60))
def test_no_cut_leaves_a_tool_result_without_its_call(seed: int) -> None:
    rng = random.Random(seed)
    history = _random_history(rng)
    size = count_model_message_tokens(history)

    trimmed = enforce_token_ceiling(history, ceiling=max(50, int(size * rng.random())))
    cut = find_safe_cutoff(history, rng.randint(0, len(history)))

    assert _orphans(drop_orphan_tool_results(trimmed)) == []
    assert _orphans(history[cut:]) == []


@pytest.mark.parametrize("seed", range(30))
async def test_compaction_never_leaves_a_tool_result_without_its_call(
    seed: int,
) -> None:
    from types import SimpleNamespace

    from app.modules.agent.infrastructure.harness.lemma.history_compaction import (
        HistoryCompactor,
    )

    rng = random.Random(seed)
    history = _random_history(rng)
    size = count_model_message_tokens(history)
    compactor = HistoryCompactor(
        model=FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart("brief")])
        ),
        trigger_tokens=max(1, int(size * rng.uniform(0.2, 0.9))),
        keep_messages=rng.randint(1, 12),
    )

    compacted = await compactor(SimpleNamespace(), list(history))

    assert _orphans(compacted) == []
    assert compacted, "a history is never compacted to nothing"


def test_a_message_is_tokenized_once_per_run() -> None:
    counter = MessageTokenCounter()
    history = _history_of(2_000)

    first = counter.count(history)
    # A message the run has already measured answers from memory; a new one is
    # measured when it arrives.
    history.append(ModelRequest(parts=[UserPromptPart(content="one more")]))

    assert counter.count(history) == first + count_model_message_tokens(history[-1:])
    assert counter.count(history) == count_model_message_tokens(history)
