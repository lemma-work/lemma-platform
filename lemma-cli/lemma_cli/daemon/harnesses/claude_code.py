from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from .base import StreamTextState
from .codex import (
    _daemon_session_invalid_payload,
    _daemon_session_started_payload,
    codex_tool_token,
    daemon_turn_timeout_seconds,
)
from .._logging import log as daemon_log
from ..mcp import (
    provider_command,
    provider_cwd_for_run,
    provider_environment,
    write_provider_mcp_files,
)
from ..process import STREAM_READER_LIMIT, drain_stream, terminate_gracefully


class ClaudeCodeHarness:
    kind = "CLAUDE_CODE"

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
        )

    async def close(self) -> None:
        pass


async def _run_claude_code_provider(
    *,
    model_name: str,
    prompt_text: str,
    session_id: str | None,
    mcp: dict[str, Any],
    event_sink: Callable[[str, Any], Awaitable[None]] | None = None,
    stop_event: asyncio.Event | None = None,
    harness_kind: str = "CLAUDE_CODE",
) -> dict[str, Any]:
    # Claude Code and Cursor (cursor-agent) emit the same stream-json shape
    # (type: system/assistant/result, session_id, message.content[].text), so a
    # single streaming runner serves both -- only the command template, cwd, and
    # MCP injection differ per harness.
    command = provider_command(
        harness_kind=harness_kind,
        model_name=model_name,
        prompt_text=prompt_text,
        mcp=mcp,
        session_id=session_id,
    )
    if not command:
        raise RuntimeError(f"No provider command configured for {harness_kind}")
    from ..mcp import provider_command_template
    stdin_text = None if "{prompt}" in provider_command_template(harness_kind) else prompt_text
    cwd = provider_cwd_for_run(harness_kind, mcp)
    # File-based harnesses (Cursor: .cursor/mcp.json) need their MCP config
    # written into the run cwd; no-op for flag-based harnesses like Claude Code.
    write_provider_mcp_files(harness_kind, cwd, mcp)
    env = provider_environment(harness_kind=harness_kind, mcp=mcp)
    daemon_log("start stream provider", {"harness_kind": harness_kind, "command": command, "cwd": str(cwd)})
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        limit=STREAM_READER_LIMIT,
    )
    stdout_parts: list[str] = []
    raw_stdout_parts: list[str] = []
    stderr_task = asyncio.create_task(drain_stream(process.stderr))
    state = StreamTextState(harness_kind=harness_kind, event_sink=event_sink)
    emitted_session_id: str | None = None
    try:
        async with asyncio.timeout(daemon_turn_timeout_seconds()):
            if stdin_text is not None and process.stdin is not None:
                process.stdin.write(stdin_text.encode())
                await process.stdin.drain()
                process.stdin.close()
            assert process.stdout is not None
            while True:
                if stop_event is not None and stop_event.is_set():
                    await terminate_gracefully(process)
                    break
                line = await process.stdout.readline()
                if not line:
                    break
                text_line = line.decode(errors="replace")
                try:
                    event = json.loads(text_line)
                except json.JSONDecodeError:
                    raw_stdout_parts.append(text_line)
                    continue
                if not isinstance(event, dict):
                    continue
                daemon_log("claude stream event", event)
                stream_session_id = _claude_stream_session_id(event)
                if (
                    stream_session_id
                    and stream_session_id != session_id
                    and stream_session_id != emitted_session_id
                    and event_sink is not None
                ):
                    emitted_session_id = stream_session_id
                    await event_sink("status", _daemon_session_started_payload(harness_kind=harness_kind, session_id=stream_session_id))
                handled_text = await _handle_claude_stream_event(event, state)
                if handled_text:
                    stdout_parts.append(handled_text)
            await process.wait()
            stderr = await stderr_task
    except TimeoutError:
        await terminate_gracefully(process)
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
        raise TimeoutError(f"{harness_kind} provider turn timed out")
    except asyncio.CancelledError:
        await terminate_gracefully(process)
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
        raise
    await state.flush(is_final=True)
    raw_stdout = "".join(raw_stdout_parts).strip()
    stdout = state.full_text.strip() or "".join(stdout_parts).strip() or raw_stdout
    stderr_text = stderr.strip()
    if (
        session_id is not None
        and process.returncode
        and _claude_saved_session_error_is_recoverable(stderr_text)
    ):
        if event_sink is not None:
            await event_sink("status", _daemon_session_invalid_payload(harness_kind=harness_kind, session_id=session_id))
        return await _run_claude_code_provider(
            model_name=model_name,
            prompt_text=prompt_text,
            session_id=None,
            mcp=mcp,
            event_sink=event_sink,
            stop_event=stop_event,
            harness_kind=harness_kind,
        )
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": int(process.returncode or 0),
        "stdout": stdout,
        "stderr": stderr_text,
        "streamed_tokens": state.streamed_tokens,
        "streamed_messages": state.streamed_messages,
    }


async def _handle_claude_stream_event(
    event: dict[str, Any],
    state: StreamTextState,
) -> str:
    event_type = str(event.get("type") or "")
    if event_type == "assistant":
        message = event.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if not isinstance(content, list):
            return ""
        text = "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
        if text:
            await state.update_text_snapshot(text)
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                await state.flush(is_final=False)
                await _emit_claude_tool_call(part, state)
        return text
    if event_type == "tool_call":
        # Cursor reports tool activity as top-level tool_call events
        # (subtype started/completed) rather than Claude's tool_use content
        # parts. Claude Code never emits this type, so handling it here is safe
        # for both.
        await state.flush(is_final=False)
        await _emit_cursor_tool_call(event, state)
        return ""
    if event_type == "result":
        result = event.get("result")
        if isinstance(result, str) and result and not state.full_text:
            await state.update_text_snapshot(result)
        await state.flush(is_final=True)
        return result if isinstance(result, str) else ""
    return ""


async def _emit_cursor_tool_call(event: dict[str, Any], state: StreamTextState) -> None:
    call_id = str(event.get("call_id") or "")
    if not call_id or state.event_sink is None:
        return
    subtype = str(event.get("subtype") or "")
    tool_call = event.get("tool_call") if isinstance(event.get("tool_call"), dict) else {}
    tool_name, tool_args = _cursor_tool_name_and_args(tool_call)
    if subtype == "started":
        if call_id in state.emitted_tool_call_ids:
            return
        state.emitted_tool_call_ids.add(call_id)
        message = {
            "role": "assistant",
            "kind": "tool_call",
            "tool_name": tool_name,
            "tool_call_id": call_id,
            "tool_args": tool_args,
            "metadata": {"tool_name": tool_name, "provider": state.harness_kind},
        }
        state.streamed_tokens = True
        await state.event_sink("token", codex_tool_token(message))
        await state.event_sink("message", message)
    elif subtype == "completed":
        if call_id in state.emitted_tool_return_ids:
            return
        state.emitted_tool_return_ids.add(call_id)
        state.streamed_messages = True
        await state.event_sink(
            "message",
            {
                "role": "tool",
                "kind": "tool_return",
                "tool_name": tool_name,
                "tool_call_id": call_id,
                "tool_result": _cursor_tool_result(tool_call),
                "metadata": {"tool_name": tool_name, "provider": state.harness_kind},
            },
        )


def _cursor_tool_name_and_args(tool_call: dict[str, Any]) -> tuple[str, Any]:
    """Pull the tool name + args from Cursor's ``tool_call`` payload.

    Shape is ``{"<kind>ToolCall": {"args": {...}, "result": ...}}`` -- e.g.
    ``shellToolCall``, ``mcpToolCall``. MCP calls carry the real tool name; for
    built-ins we derive it from the key ("shellToolCall" -> "shell").
    """
    for key, value in tool_call.items():
        if not isinstance(value, dict):
            continue
        args = value.get("args") if isinstance(value.get("args"), dict) else {}
        name = (
            value.get("name")
            or (args.get("name") if isinstance(args, dict) else None)
            or (args.get("toolName") if isinstance(args, dict) else None)
            or (key[: -len("ToolCall")] if key.endswith("ToolCall") else key)
        )
        return str(name), args
    return "tool", {}


def _cursor_tool_result(tool_call: dict[str, Any]) -> Any:
    for value in tool_call.values():
        if isinstance(value, dict) and "result" in value:
            return value.get("result")
    return None


async def _emit_claude_tool_call(part: dict[str, Any], state: StreamTextState) -> None:
    tool_name = part.get("name")
    tool_call_id = part.get("id")
    if not isinstance(tool_name, str) or not isinstance(tool_call_id, str):
        return
    if tool_call_id in state.emitted_tool_call_ids:
        return
    state.emitted_tool_call_ids.add(tool_call_id)
    from .._utils import parse_jsonish
    tool_call = {
        "role": "assistant",
        "kind": "tool_call",
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_args": parse_jsonish(part.get("input", {})),
        "metadata": {
            "tool_name": tool_name,
            "provider": "CLAUDE_CODE",
        },
    }
    if state.event_sink is not None:
        state.streamed_tokens = True
        await state.event_sink("token", codex_tool_token(tool_call))
        await state.event_sink("message", tool_call)


def _claude_stream_session_id(event: dict[str, Any]) -> str | None:
    for key in ("session_id", "sessionId"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = event.get("message")
    if isinstance(message, dict):
        for key in ("session_id", "sessionId"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _claude_saved_session_error_is_recoverable(stderr: str) -> bool:
    detail = stderr.lower()
    return "session" in detail and any(
        marker in detail
        for marker in ("not found", "missing", "expired", "invalid", "resume")
    )


def _provider_prompt_text(*, system_prompt: str, user_prompt: str) -> str:
    if not system_prompt:
        return user_prompt
    conversation = (
        user_prompt
        if user_prompt.lstrip().startswith("#")
        else "# Conversation\n" + user_prompt
        if user_prompt
        else ""
    )
    return "\n\n".join(part for part in (system_prompt, conversation) if part.strip())
