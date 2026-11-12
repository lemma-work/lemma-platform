"""A group's background is bounded, keeping the newest and saying what it cut.

It rides along with the prompt on every step of the run, and neither the number
of lines nor the length of one was capped -- a pasted log or a long thread
became most of the prompt.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.prompt.channel_context import (
    channel_context_block,
)

pytestmark = pytest.mark.unit


def _lines(count: int, *, text: str = "hello") -> list[dict[str, object]]:
    return [
        {"author": f"P{index}", "text": f"{text} {index}"} for index in range(count)
    ]


def test_a_long_thread_keeps_its_newest_lines_and_counts_the_rest():
    block = channel_context_block({"channel_context": _lines(100)})

    assert block is not None
    assert '- P99: "hello 99"' in block
    assert '- P0: "hello 0"' not in block
    assert "(70 earlier messages not shown)" in block
    assert block.count("- P") == 30


def test_one_long_message_is_clipped():
    block = channel_context_block(
        {"channel_context": [{"author": "Ana", "text": "x" * 20_000}]}
    )

    assert block is not None
    assert len(block) < 1_000
    assert "…" in block


def test_a_short_thread_is_shown_whole():
    block = channel_context_block({"channel_context": _lines(3)})

    assert block is not None
    assert "not shown" not in block
    assert block.count("- P") == 3
