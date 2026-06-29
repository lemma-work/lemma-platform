from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .claude_code import _provider_prompt_text
from .codex import daemon_turn_timeout_seconds
from .._logging import log as daemon_log
from ..mcp import (
    provider_command,
    provider_command_template,
    provider_cwd_for_run,
    provider_environment,
    write_provider_mcp_files,
)
from ..process import STREAM_READER_LIMIT, terminate_gracefully


class AntigravityHarness:
    """Antigravity (``agy``) harness.

    ``agy`` has no stream-json mode -- ``agy -p`` runs a single prompt and prints
    the final response as plain text. So this is a one-shot runner: write the
    workspace MCP config (``.agents/mcp_config.json``), feed the prompt on stdin,
    and return stdout as the assistant message. Non-streaming: the bridge emits
    the full text once the turn completes.
    """

    kind = "ANTIGRAVITY"

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
        command = provider_command(
            harness_kind="ANTIGRAVITY",
            model_name=model_name,
            prompt_text=prompt_text,
            mcp=mcp,
            session_id=session_id,
        )
        if not command:
            raise RuntimeError("No provider command configured for ANTIGRAVITY")
        stdin_text = None if "{prompt}" in provider_command_template("ANTIGRAVITY") else prompt_text
        cwd = provider_cwd_for_run("ANTIGRAVITY", mcp)
        write_provider_mcp_files("ANTIGRAVITY", cwd, mcp)
        env = provider_environment(harness_kind="ANTIGRAVITY", mcp=mcp)
        daemon_log("start antigravity provider", {"harness_kind": "ANTIGRAVITY", "command": command, "cwd": str(cwd)})
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            limit=STREAM_READER_LIMIT,
        )
        try:
            async with asyncio.timeout(daemon_turn_timeout_seconds()):
                stdout_bytes, stderr_bytes = await process.communicate(
                    input=(stdin_text or "").encode() if stdin_text is not None else None
                )
        except (TimeoutError, asyncio.CancelledError):
            await terminate_gracefully(process)
            raise
        stdout = (stdout_bytes or b"").decode(errors="replace").strip()
        stderr = (stderr_bytes or b"").decode(errors="replace").strip()
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": int(process.returncode or 0),
            "stdout": stdout,
            "stderr": stderr,
            "streamed_tokens": False,
            "streamed_messages": False,
        }

    async def close(self) -> None:
        pass
