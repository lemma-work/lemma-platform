"""Unit tests for opencode tool-part extraction + tool_call/tool_return emission.

The opencode harness surfaces tool calls/outputs to Lemma by reading the
structured ``parts`` of each polled message and emitting TOOL_CALL/TOOL_RETURN
events (like codex). These lock that behavior: server-prefix normalization, and
emit-once-per-callID dedup across polls.
"""

from __future__ import annotations

import asyncio
import json

from lemma_cli.daemon.harnesses.base import StreamTextState
from lemma_cli.daemon.harnesses.opencode import (
    _opencode_tool_parts,
    _strip_mcp_server_prefix,
)


def test_strip_mcp_server_prefix() -> None:
    # opencode exposes an MCP server's tool as "<server>_<tool>".
    assert (
        _strip_mcp_server_prefix("lemma_tools_lemma_exec_command", ("lemma_tools",))
        == "lemma_exec_command"
    )
    # Already-canonical names and unknown servers are left alone.
    assert _strip_mcp_server_prefix("lemma_execute_python", ("lemma_tools",)) == (
        "lemma_execute_python"
    )
    assert _strip_mcp_server_prefix("other_tool", ()) == "other_tool"


def test_opencode_tool_parts_extracts_assistant_tool_parts_and_normalizes() -> None:
    messages = [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "hi"}]},
        {
            "info": {"role": "assistant"},
            "parts": [
                {"type": "text", "text": "thinking"},
                {
                    "type": "tool",
                    "callID": "c1",
                    "tool": "lemma_tools_lemma_execute_python",
                    "state": {"status": "completed", "input": {"code": "6*7"}, "output": "42"},
                },
            ],
        },
    ]
    parts = _opencode_tool_parts(messages, ("lemma_tools",))
    assert len(parts) == 1
    assert parts[0]["tool"] == "lemma_execute_python"
    assert parts[0]["callID"] == "c1"


def test_update_tool_parts_emits_call_then_return_once() -> None:
    async def run() -> None:
        events: list[tuple[str, dict]] = []

        async def sink(event_type: str, data: dict) -> None:
            events.append((event_type, data))

        state = StreamTextState(harness_kind="OPENCODE", event_sink=sink)
        running = [
            {
                "callID": "c1",
                "tool": "lemma_execute_python",
                "state": {"status": "running", "input": {"code": "6*7"}},
            }
        ]
        completed = [
            {
                "callID": "c1",
                "tool": "lemma_execute_python",
                "state": {"status": "completed", "input": {"code": "6*7"}, "output": "42"},
            }
        ]
        await state.update_tool_parts(running)  # poll 1 -> tool_call (token + message)
        await state.update_tool_parts(completed)  # poll 2 -> tool_return (message)
        await state.update_tool_parts(completed)  # poll 3 -> nothing new

        messages = [data for (kind, data) in events if kind == "message"]
        tokens = [data for (kind, data) in events if kind == "token"]
        tool_calls = [m for m in messages if m.get("kind") == "tool_call"]
        tool_returns = [m for m in messages if m.get("kind") == "tool_return"]

        assert len(tool_calls) == 1
        assert tool_calls[0]["tool_name"] == "lemma_execute_python"
        assert tool_calls[0]["role"] == "assistant"
        assert tool_calls[0]["tool_args"] == {"code": "6*7"}
        assert len(tool_returns) == 1
        assert tool_returns[0]["role"] == "tool"
        assert tool_returns[0]["tool_result"] == "42"
        # A streamed tool token is emitted so the SSE tool-stream surface sees it.
        assert any(
            tok.get("kind") == "tool"
            and json.loads(tok["data"])["tool_name"] == "lemma_execute_python"
            for tok in tokens
        )

    asyncio.run(run())
