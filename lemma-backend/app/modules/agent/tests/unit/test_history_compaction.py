"""Compaction on OpenAI-compatible providers (the production path).

Fireworks has no native `CompactionPart`, so keeping a long conversation inside
the context window is entirely ours to do. Two things have to hold: the count
has to be honest, and a failed compaction must never become a provider
rejection.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.modules.agent.infrastructure.harnesses.history import (
    enforce_token_ceiling,
    find_safe_cutoff,
)
from app.modules.agent.services.history_tokens import (
    count_model_message_tokens,
    count_text_tokens,
)

pytestmark = pytest.mark.unit


def _text(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _tool_exchange(call_id: str, payload: str) -> list[object]:
    return [
        ModelResponse(
            parts=[ToolCallPart(tool_name="run", args={"x": 1}, tool_call_id=call_id)]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="run", content=payload, tool_call_id=call_id)
            ]
        ),
    ]


class TestTokenCounting:
    def test_dense_agent_content_is_not_under_counted(self) -> None:
        """The whole reason for a real tokenizer.

        `len(text)/4` under-counts every kind of content an agent actually
        accumulates — JSON by ~42%, base64 ~48%, UUIDs ~67% — so a 100k trigger
        fired at 140k+ of real prompt and the provider rejected the request
        before compaction ever ran.
        """
        uuids = "3f2504e0-4f89-11d3-9a0c-0305e82c3301 " * 60
        minified_json = '{"id":"a1b2c3","values":[1,2,3],"ok":true}' * 25

        for sample in (uuids, minified_json):
            estimate = len(sample) // 4
            assert count_text_tokens(sample) > estimate * 1.2

    def test_tool_call_arguments_and_results_are_counted(self) -> None:
        """Tool payloads are structured, not `content` strings — the easiest
        thing for a naive counter to miss, and the bulk of a coding transcript."""
        bare = count_model_message_tokens([_text("hi")])
        with_tools = count_model_message_tokens(
            [_text("hi"), *_tool_exchange("c1", "x" * 400)]
        )
        assert with_tools > bare + 50

    def test_empty_history_costs_nothing(self) -> None:
        assert count_model_message_tokens([]) == 0


class TestSafeCutoff:
    def test_a_cutoff_never_orphans_a_tool_result(self) -> None:
        """Truncating between a tool call and its result trades a context-length
        error for a validation error — providers reject the orphaned result."""
        messages = [_text("go"), *_tool_exchange("c1", "out"), _text("next")]

        # Index 2 is the ToolReturn; the cutoff must move past it.
        assert find_safe_cutoff(messages, 2) == 3

    def test_a_cutoff_that_is_already_safe_does_not_move(self) -> None:
        messages = [_text("go"), *_tool_exchange("c1", "out"), _text("next")]
        assert find_safe_cutoff(messages, 1) == 1

    def test_the_cutoff_only_moves_forward(self) -> None:
        """Moving backwards could grow the history and let the caller loop."""
        messages = [_text("a"), *_tool_exchange("c1", "out")]
        assert find_safe_cutoff(messages, 2) >= 2


class TestCeilingGuard:
    def test_history_under_the_ceiling_is_untouched(self) -> None:
        messages = [_text("small")]
        assert enforce_token_ceiling(messages, ceiling=10_000) == messages

    def test_an_oversized_history_is_brought_under_the_ceiling(self) -> None:
        """The backstop for the summarizer swallowing its own failure and
        returning the original, oversized history."""
        messages = [_text("filler " * 500) for _ in range(40)]
        assert count_model_message_tokens(messages) > 5_000

        trimmed = enforce_token_ceiling(messages, ceiling=5_000)

        assert count_model_message_tokens(trimmed) <= 5_000
        assert len(trimmed) < len(messages)
        # The most recent turn always survives — that is the actual question.
        assert trimmed[-1] is messages[-1]

    def test_trimming_keeps_tool_pairs_intact(self) -> None:
        messages: list[object] = []
        for index in range(30):
            messages.append(_text(f"turn {index} " + "filler " * 200))
            messages.extend(_tool_exchange(f"c{index}", "result " * 200))

        trimmed = enforce_token_ceiling(messages, ceiling=4_000)

        # No ToolReturnPart may appear without its ToolCallPart earlier.
        seen_calls: set[str] = set()
        for message in trimmed:
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    seen_calls.add(part.tool_call_id)
                elif isinstance(part, ToolReturnPart):
                    assert part.tool_call_id in seen_calls, (
                        "a tool result was kept without its call — the provider "
                        "rejects this"
                    )

    def test_a_ceiling_of_zero_disables_the_guard(self) -> None:
        messages = [_text("filler " * 500) for _ in range(20)]
        assert enforce_token_ceiling(messages, ceiling=0) == messages

    def test_an_impossible_ceiling_still_returns_something_sendable(self) -> None:
        """Better a too-large request than an empty one: a stripped-to-nothing
        history is not a smaller request, it is a broken one."""
        messages = [_text("filler " * 500) for _ in range(10)]

        trimmed = enforce_token_ceiling(messages, ceiling=1)

        assert trimmed
        assert trimmed[-1] is messages[-1]


def test_the_ceiling_guard_is_wired_after_the_summarizer() -> None:
    """Order matters: the guard exists to catch what the summarizer misses."""
    from app.modules.agent.domain.value_objects import HarnessOptions
    from app.modules.agent.infrastructure.harnesses.history import (
        build_history_processors,
    )

    processors = build_history_processors(
        HarnessOptions(model_name="glm-4.6"), summarization_model="openai:gpt-4.1"
    )

    assert processors[-1].__name__ == "_ceiling_guard"


@pytest.mark.asyncio
async def test_the_guard_trims_when_summarization_returned_oversized_history() -> None:
    """`pydantic_ai_summarization` catches its own LLM failures and returns the
    ORIGINAL messages with skip_reason="failed" — safe for the data, fatal for
    the next request. This is the only thing standing between that and a 400."""
    from app.modules.agent.domain.value_objects import HarnessOptions
    from app.modules.agent.infrastructure.harnesses.history import (
        build_history_processors,
    )

    processors = build_history_processors(
        HarnessOptions(
            model_name="glm-4.6",
            history_summarization_enabled=False,
            history_hard_token_ceiling=3_000,
        ),
        summarization_model="openai:gpt-4.1",
    )
    guard = processors[-1]

    oversized = [_text("filler " * 400) for _ in range(30)]
    result = await guard(oversized)

    assert count_model_message_tokens(result) <= 3_000
