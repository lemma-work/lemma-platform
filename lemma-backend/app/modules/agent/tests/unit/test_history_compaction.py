"""Compaction on OpenAI-compatible providers (the production path).

Fireworks has no native `CompactionPart`, so keeping a long conversation inside
the context window is entirely ours to do. Two things have to hold: the count
has to be honest, and a failed compaction must never become a provider
rejection.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    BinaryContent,
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

    # Compaction first, the ceiling backstop after it, and the provider shape
    # guarantee last so it sees whatever those produced.
    names = [
        getattr(processor, "__name__", type(processor).__name__)
        for processor in processors
    ]
    assert names.index("_ceiling_guard") > names.index("HistoryCompactor")
    assert names[-1] == "_ensure_leading_user_message"


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
    guard = processors[-2]

    oversized = [_text("filler " * 400) for _ in range(30)]
    result = await guard(oversized)

    assert count_model_message_tokens(result) <= 3_000


def _png(size: int) -> bytes:
    """Bytes that tokenize like a real image: high-entropy, not a run of zeros."""
    return b"\x89PNG\r\n" + (bytes(range(256)) * (size // 256 + 1))[:size]


def _image_exchange(
    caption: str, *, size: int = 130_000, call_id: str = "img1"
) -> list[object]:
    """A `view_image` round-trip in the shape pydantic-ai actually produces.

    The binary never arrives as bare `bytes`: it is a `BinaryContent` sitting in
    a list next to its caption. That is precisely the shape the old top-level
    `isinstance(value, bytes)` guard could not see.
    """
    return [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="view_image",
                    args={"workspace_file_path": "shot.png"},
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="view_image",
                    content=[
                        caption,
                        BinaryContent(data=_png(size), media_type="image/png"),
                    ],
                    tool_call_id=call_id,
                )
            ]
        ),
    ]


class TestImagesAreNotCountedAsText:
    """The regression suite for a conversation that lost its own task.

    A 129KB screenshot counted as 277k tokens against a 110k ceiling. The guard
    then head-dropped a healthy 40-message history to 4 messages to fit a number
    that was never real, taking the user's original request with it, and the
    agent spent the next hour building something nobody asked for.
    """

    def test_an_image_costs_what_a_vision_model_charges_not_its_bytes(self) -> None:
        assert count_model_message_tokens(_image_exchange("Read image")) < 3_000

    def test_image_cost_does_not_grow_with_file_size(self) -> None:
        """The old counter billed per byte. A vision model bills per image."""
        small = count_model_message_tokens(_image_exchange("shot", size=10_000))
        large = count_model_message_tokens(_image_exchange("shot", size=400_000))

        assert small == large

    def test_an_image_in_the_tail_does_not_evict_the_conversation(self) -> None:
        """The incident, in miniature: a screenshot at the end of a healthy
        history must not cost the user the request that started it."""
        messages: list[object] = [_text("Explain this architecture as a video")]
        for index in range(12):
            messages.extend(_tool_exchange(f"c{index}", "build output " * 100))
        messages.extend(_image_exchange("Successfully read image", size=200_000))

        trimmed = enforce_token_ceiling(messages, ceiling=110_000)

        assert trimmed == messages
        assert any(
            isinstance(part, UserPromptPart) and "video" in str(part.content)
            for message in trimmed
            for part in message.parts
        )

    def test_binary_nested_deeper_than_a_list_is_still_not_text(self) -> None:
        """A document viewer returns one payload per page, inside a structure."""
        pages = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="pod_view_document_pages",
                    content={
                        "pages": [
                            {
                                "page_number": number,
                                "image": BinaryContent(
                                    data=_png(80_000), media_type="image/png"
                                ),
                            }
                            for number in range(10)
                        ]
                    },
                    tool_call_id="doc1",
                )
            ]
        )

        assert count_model_message_tokens([pages]) < 25_000

    def test_structured_tool_results_are_still_counted(self) -> None:
        """Excluding binary must not excuse the counter from structured text --
        under-counting JSON is the failure the real tokenizer was added for."""
        rows = [{"id": index, "name": f"customer-{index}"} for index in range(200)]
        message = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="pod_query",
                    content={"rows": rows},
                    tool_call_id="q1",
                )
            ]
        )

        assert count_model_message_tokens([message]) > 1_000


def test_a_known_size_does_not_change_what_the_guard_decides() -> None:
    """`known_size` exists to skip one re-tokenisation on the request path, so
    it must be an optimisation and never a behaviour change."""
    messages = [_text("filler " * 500) for _ in range(20)]
    measured = count_model_message_tokens(messages)

    assert enforce_token_ceiling(messages, ceiling=5_000) == enforce_token_ceiling(
        messages, ceiling=5_000, known_size=measured
    )


class TestTheHistoryOpensWithSomethingProvidersAccept:
    """Anthropic requires the first message to be a user turn.

    Trimming and compaction both cut at a point that is safe for tool pairing,
    which says nothing about role -- so the backstop that exists to prevent a
    provider rejection could cause one.
    """

    def _guard(self):
        from app.modules.agent.domain.value_objects import HarnessOptions
        from app.modules.agent.infrastructure.harnesses.history import (
            build_history_processors,
        )

        processors = build_history_processors(
            HarnessOptions(model_name="claude-sonnet-4"),
            summarization_model="openai:gpt-4.1",
        )
        return processors[-1]

    @pytest.mark.asyncio
    async def test_a_history_starting_with_an_assistant_turn_is_fixed(self) -> None:
        history = [*_tool_exchange("c1", "out")]

        result = await self._guard()(history)

        assert isinstance(result[0], ModelRequest)

    @pytest.mark.asyncio
    async def test_a_history_already_starting_with_a_user_turn_is_untouched(
        self,
    ) -> None:
        history = [_text("go"), *_tool_exchange("c1", "out")]

        result = await self._guard()(history)

        assert result == history

    @pytest.mark.asyncio
    async def test_an_empty_history_is_left_alone(self) -> None:
        assert await self._guard()([]) == []

    @pytest.mark.asyncio
    async def test_the_tool_pair_is_not_disturbed(self) -> None:
        history = [*_tool_exchange("c1", "out")]

        result = await self._guard()(history)

        assert result[1:] == history
