from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .claude_code import _provider_prompt_text, _run_claude_code_provider


class CursorHarness:
    """Cursor (``cursor-agent``) harness.

    cursor-agent's ``--output-format stream-json`` emits the same event shape as
    Claude Code (``type: system/assistant/result``, ``session_id``,
    ``message.content[].text``), so it reuses the shared stream-json runner. The
    only harness-specific bits live in config: the command template, the run
    cwd, and file-based MCP injection (``.cursor/mcp.json``).
    """

    kind = "CURSOR"

    async def run(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        session_id: str | None,
        mcp: dict[str, Any],
        event_sink: Callable[[str, Any], Awaitable[None]] | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        prompt_text = _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)
        return await _run_claude_code_provider(
            model_name=model_name,
            prompt_text=prompt_text,
            session_id=session_id,
            mcp=mcp,
            event_sink=event_sink,
            stop_event=stop_event,
            harness_kind="CURSOR",
        )

    async def close(self) -> None:
        pass
