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
        """Bounded on both sides. One-sided, this passed with the per-image
        price set to 1 — and a wrong per-image price is the entire incident."""
        cost = count_model_message_tokens(_image_exchange("Read image"))

        assert 1_000 < cost < 3_000

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

        # Two-sided: charging *nothing* for ten images also passed before, so
        # deleting the dict recursion in `_binary_tokens` went unnoticed.
        cost = count_model_message_tokens([pages])
        assert 10 * 1_000 < cost < 25_000

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


def test_a_known_size_skips_the_measurement_it_was_given(monkeypatch) -> None:
    """`known_size` exists to skip one re-tokenisation on the request path.

    Comparing two calls to each other cannot show that: both sides move
    together, so deleting the short-circuit entirely left the old version of
    this test passing. Count the measurements instead.
    """
    from app.modules.agent.infrastructure.harnesses import history as history_module

    calls: list[int] = []
    real = history_module.count_model_message_tokens

    def counted(messages):
        calls.append(1)
        return real(messages)

    monkeypatch.setattr(history_module, "count_model_message_tokens", counted)
    messages = [_text("filler " * 500) for _ in range(20)]
    measured = real(messages)

    calls.clear()
    enforce_token_ceiling(messages, ceiling=5_000)
    without = len(calls)
    calls.clear()
    enforce_token_ceiling(messages, ceiling=5_000, known_size=measured)
    with_known = len(calls)

    assert with_known == without - 1


def test_a_known_size_under_the_ceiling_short_circuits(monkeypatch) -> None:
    """The contract callers rely on: pass a size and it is believed."""
    from app.modules.agent.infrastructure.harnesses import history as history_module

    monkeypatch.setattr(
        history_module,
        "count_model_message_tokens",
        lambda messages: pytest.fail("should not have measured"),
    )
    messages = [_text("filler " * 500) for _ in range(20)]

    assert enforce_token_ceiling(messages, ceiling=5_000, known_size=10) == messages


class TestTheCeilingGuardActuallyEnforces:
    """Found by adversarial review of this branch.

    The guard cut the unpinned middle, found the result still over the ceiling,
    and returned it anyway: twelve of seventeen messages destroyed *and* a
    prompt 43% over the window — the provider rejection it exists to prevent,
    paid for twice — while logging that it had enforced one.
    """

    def _huge(self) -> list[object]:
        messages: list[object] = [
            _text("U0: reconcile the ledger"),
            _text("U1: also check refunds — do NOT email anyone"),
        ]
        for index in range(7):
            messages.extend(_tool_exchange(f"c{index}", "x" * 240_000))
        return messages

    def test_it_gets_under_the_ceiling(self) -> None:
        trimmed = enforce_token_ceiling(self._huge(), ceiling=117_760)

        assert count_model_message_tokens(trimmed) <= 117_760

    def test_every_pinned_turn_survives_not_just_the_first(self) -> None:
        """The fallback kept exactly one pin. The turn it dropped is the kind
        whose loss produces the incident: a mid-conversation correction, or a
        negative constraint like 'do NOT email anyone'."""
        trimmed = enforce_token_ceiling(self._huge(), ceiling=117_760)

        kept = " ".join(
            str(part.content)
            for message in trimmed
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        assert "reconcile the ledger" in kept
        assert "do NOT email anyone" in kept

    def test_the_model_is_told_messages_were_dropped(self) -> None:
        """Every other cap on this branch announces itself. This was the largest
        and the only silent one, so the model read a history where two unrelated
        turns sat adjacent and reasoned confidently from it."""
        trimmed = enforce_token_ceiling(self._huge(), ceiling=117_760)

        assert any(
            "did not fit" in str(part.content)
            for message in trimmed
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )

    def test_it_gives_up_pinned_turns_when_they_alone_do_not_fit(self) -> None:
        """Stage two. Replacing it with `pins[:1]` left the whole class passing,
        including the test written for exactly that."""
        messages: list[object] = [
            _text(f"U{index}: " + "word " * 1_600) for index in range(20)
        ]
        # Enough trailing tool traffic that the kept tail holds no user turn at
        # all, so the newest one can only survive by being pinned. Without this
        # it sits inside the tail and survives even when stage two is gutted.
        for index in range(4):
            messages.extend(_tool_exchange(f"c{index}", "small"))

        trimmed = enforce_token_ceiling(messages, ceiling=30_000)

        assert count_model_message_tokens(trimmed) <= 30_000
        kept = " ".join(
            str(part.content)
            for message in trimmed
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        # The first is the request; the newest is what they just said.
        assert "U0:" in kept
        assert "U19:" in kept

    def test_it_shrinks_the_recent_tail_when_that_is_all_that_is_left(self) -> None:
        """Stage three. Deleting the loop left the class passing."""
        messages: list[object] = [_text("U0: the request")]
        for index in range(4):
            messages.extend(_tool_exchange(f"c{index}", "x" * 400_000))

        trimmed = enforce_token_ceiling(messages, ceiling=60_000)

        assert count_model_message_tokens(trimmed) <= 60_000

    def test_the_notice_never_lands_between_a_call_and_its_result(self) -> None:
        """Providers require a tool result to follow its call. Moving the notice
        one slot later put it between them."""
        messages: list[object] = [_text("U0: the request")]
        for index in range(9):
            messages.extend(_tool_exchange(f"c{index}", "x" * 200_000))

        trimmed = enforce_token_ceiling(messages, ceiling=117_760)

        for position, message in enumerate(trimmed[:-1]):
            calls = [p for p in message.parts if isinstance(p, ToolCallPart)]
            if not calls:
                continue
            following = trimmed[position + 1].parts
            assert any(isinstance(p, ToolReturnPart) for p in following), (
                "a tool call is not immediately followed by its result"
            )

    def test_the_newest_turn_is_still_last(self) -> None:
        """A notice after the final turn competes with the thing being answered.
        Reachable when every surviving message is pinned."""
        messages = [_text("filler " * 400) for _ in range(30)]

        trimmed = enforce_token_ceiling(messages, ceiling=2_000)

        assert trimmed[-1] is messages[-1]


class TestCountingSurvivesUnfamiliarShapes:
    def test_bytes_under_any_field_name_are_not_charged_as_text(self) -> None:
        """The guard being one attribute wide is how the original defect worked.
        A payload whose bytes hang off another name was charged at its repr —
        387k tokens for a 240KB image, the same failure by a different route.
        """
        from pydantic import BaseModel

        class Odd(BaseModel):
            model_config = {"arbitrary_types_allowed": True}
            payload: bytes

        message = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="t",
                    content=Odd(payload=b"\xff" * 240_000),
                    tool_call_id="c",
                )
            ]
        )

        assert count_model_message_tokens([message]) < 5_000

    def test_a_multimodal_user_turn_is_still_the_users(self) -> None:
        """`_is_synthetic` only inspected string content, so a list fell through
        to 'synthetic' — and a user turn with an attachment stopped being pinned,
        which is the one kind of message this module exists to keep."""
        from pydantic_ai.messages import BinaryContent

        from app.modules.agent.infrastructure.harnesses.history import (
            is_pinned_message,
        )

        message = ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        "look at this",
                        BinaryContent(
                            data=b"\x89PNG" + b"\x00" * 100, media_type="image/png"
                        ),
                    ]
                )
            ]
        )

        assert is_pinned_message(message)
