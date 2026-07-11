from __future__ import annotations

from typing import Any

import pytest

from lemma_cli.daemon.harnesses.base import StreamTextState
from lemma_cli.daemon.harnesses.claude_code import _handle_claude_stream_event


async def _capture_tool_result(content: Any, *, is_error: bool = False) -> list[dict]:
    events: list[tuple[str, dict]] = []

    async def sink(event_type: str, data: dict) -> None:
        events.append((event_type, data))

    state = StreamTextState(harness_kind="CLAUDE_CODE", event_sink=sink)
    call_event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "mcp__lemma_tools__lemma_display_resource",
                    "input": {"request": {"type": "TABLE", "name": "orders"}},
                }
            ]
        },
    }
    return_event = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }

    await _handle_claude_stream_event(call_event, state)
    await _handle_claude_stream_event(return_event, state)
    await _handle_claude_stream_event(return_event, state)
    return [data for event_type, data in events if event_type == "message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"success":true,"url":"/widgets/1"}', {"success": True, "url": "/widgets/1"}),
        ([{"type": "text", "text": '{"count":3}'}], {"count": 3}),
        (
            [
                {"type": "text", "text": "caption"},
                {"type": "image", "data": "abc"},
            ],
            [
                {"type": "text", "text": "caption"},
                {"type": "image", "data": "abc"},
            ],
        ),
        ("plain output", "plain output"),
    ],
)
async def test_claude_user_tool_result_emits_one_paired_return(
    content: Any, expected: Any
) -> None:
    messages = await _capture_tool_result(content)

    assert [message["kind"] for message in messages] == ["tool_call", "tool_return"]
    assert messages[0]["tool_call_id"] == messages[1]["tool_call_id"] == "toolu_1"
    assert messages[1]["tool_name"] == "mcp__lemma_tools__lemma_display_resource"
    assert messages[1]["tool_result"] == expected


@pytest.mark.asyncio
async def test_claude_error_tool_result_is_a_failed_return() -> None:
    messages = await _capture_tool_result("permission denied", is_error=True)

    assert messages[-1]["tool_result"] == {
        "success": False,
        "error": "permission denied",
    }
