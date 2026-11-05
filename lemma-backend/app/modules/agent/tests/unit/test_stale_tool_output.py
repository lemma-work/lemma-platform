"""A large tool result the model read long ago stops costing its full size.

Tool output stayed verbatim for the rest of a run, so one large file read early
was re-sent on every request after it and brought compaction forward. Old
results are cut to their head and tail with a way back to the rest -- in steps
of whole blocks of messages, so the provider's prompt cache is not invalidated
on every request.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.modules.agent.infrastructure.harness.lemma.stale_tool_output import (
    BOUNDARY_BLOCK,
    CLIP_ABOVE_CHARS,
    RECENT_MESSAGES,
    clip_stale_tool_returns,
)
from app.modules.agent.infrastructure.providers.tokens import count_model_message_tokens

pytestmark = pytest.mark.unit

_BIG = "line of a large file\n" * 2_000


def _exchange(call_id: str, content: object) -> list[ModelMessage]:
    return [
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="pod_read_file", args={}, tool_call_id=call_id)
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="pod_read_file", content=content, tool_call_id=call_id
                )
            ]
        ),
    ]


def _history(steps: int, *, big_at: int = 0) -> list[ModelMessage]:
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="go")])]
    for step in range(steps):
        messages.extend(_exchange(f"c{step}", _BIG if step == big_at else "small"))
    return messages


def _content(message: ModelMessage) -> object:
    return message.parts[0].content  # type: ignore[union-attr]


def test_an_old_large_result_is_cut_to_a_preview_with_a_way_back() -> None:
    history = _history(20)

    clipped = clip_stale_tool_returns(history)

    preview = _content(clipped[2])
    assert isinstance(preview, str)
    assert len(preview) < CLIP_ABOVE_CHARS
    assert preview.startswith("line of a large file")
    assert "Call the tool again" in preview
    assert count_model_message_tokens(clipped) < count_model_message_tokens(history) / 3


def test_a_recent_large_result_is_left_whole() -> None:
    history = _history(20, big_at=19)

    clipped = clip_stale_tool_returns(history)

    assert _content(clipped[-1]) == _BIG


def test_small_results_and_untouched_messages_keep_their_identity() -> None:
    history = _history(20)

    clipped = clip_stale_tool_returns(history)

    assert clipped[4] is history[4]
    assert clipped[1] is history[1]


def test_it_is_idempotent() -> None:
    once = clip_stale_tool_returns(_history(20))

    assert clip_stale_tool_returns(once) == once


def test_the_clipped_prefix_only_changes_once_per_block() -> None:
    """A provider caches a prefix: rewriting history one message further on
    every request would invalidate the cache from that point every time."""
    history = _history(40, big_at=-1)
    for step in range(40):
        history[2 + 2 * step] = _exchange(f"c{step}", _BIG)[1]

    lengths = range(RECENT_MESSAGES + 1, len(history))
    boundaries = []
    for length in lengths:
        clipped = clip_stale_tool_returns(history[:length])
        boundaries.append(
            sum(1 for a, b in zip(clipped, history[:length]) if a is not b)
        )

    changes = sum(1 for a, b in zip(boundaries, boundaries[1:]) if a != b)
    assert changes <= len(lengths) // BOUNDARY_BLOCK + 1


def test_an_image_result_is_left_for_the_image_detacher() -> None:
    image = [
        "Read the image",
        BinaryContent(data=b"\x89PNG" * 5_000, media_type="image/png"),
    ]
    history = _history(20)
    history[2] = _exchange("img", image)[1]

    clipped = clip_stale_tool_returns(history)

    assert clipped[2] is history[2]
