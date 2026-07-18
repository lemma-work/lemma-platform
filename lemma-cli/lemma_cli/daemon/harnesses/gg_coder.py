from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .base import StreamTextState
from .codex import (
    _daemon_session_started_payload,
    codex_tool_token,
    daemon_turn_timeout_seconds,
)
from .._logging import log as daemon_log
from ..mcp import (
    provider_command,
    provider_command_template,
    provider_cwd_for_run,
    provider_environment,
    write_provider_mcp_files,
)
from ..process import STREAM_READER_LIMIT, drain_stream, terminate_gracefully

# Model names accepted on the CLI. GG Coder accepts a wide range across its
# 8 providers; we don't try to validate here -- the upstream binary will surface
# any errors via the `error` JSON event we forward.
_MODEL_HINT = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")


class GgCoderMaxTurnsReached(RuntimeError):
    """``ggcoder --json`` signalled that it hit the configured ``--max-turns``.

    Distinct from ``TimeoutError`` so the outer ``except TimeoutError`` that
    guards ``daemon_turn_timeout_seconds()`` doesn't blanket-replace our
    user-facing message with the generic "provider turn timed out" string.
    """


class GgCoderHarness:
    """GG Coder (``ggcoder --json``) harness.

    GG Coder is a coding agent from KenKaiii/gg-framework. When launched with
    ``--json`` it streams NDJSON ``AgentSession`` events on stdout
    (``text_delta``, ``thinking_delta``, ``tool_call_start``,
    ``tool_call_end``, ``turn_end``, ``agent_done``) and exits when the turn
    completes. The harness pipes the prompt on stdin and translates each NDJSON
    event into Lemma `token` / `message` events using the same shapes the
    codex + cursor harnesses emit.
    """

    kind = "GG_CODER"

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
        prompt_text = _gg_coder_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)
        return await _run_gg_coder_provider(
            model_name=model_name,
            prompt_text=prompt_text,
            session_id=session_id,
            mcp=mcp,
            event_sink=event_sink,
            stop_event=stop_event,
        )

    async def close(self) -> None:
        pass


def _gg_coder_resume_token(session_id: str | None) -> str | None:
    """Return a ``--resume`` path for ``ggcoder`` if ``session_id`` looks usable.

    GG Coder's upstream session id is opaque (a UUID). We only forward
    ``--resume`` when the caller explicitly passed a path that points at an
    existing file; otherwise the harness starts a fresh upstream session.
    """
    if not session_id:
        return None
    # Opaque ids have no path separators. Anything with one is treated as a path.
    if "/" not in session_id and "\\" not in session_id:
        return None
    candidate = session_id
    if os.path.isfile(candidate):
        return candidate
    return None


def _strip_text_envelope(text: Any) -> str:
    """Coerce a ``text_delta`` payload into plain prose.

    The upstream ``ggcoder --json`` event contract says ``text`` is a plain
    string. Defense in depth: if upstream ever ships a dict-shaped value
    (which would surface to the chat bubble as ``{...}``), pull the
    best-effort readable string out and drop the JSON envelope.

    Strings pass through verbatim except for embedded envelope-shaped
    fragments (e.g. an upstream regression that emits ``"data": "..."``
    inline); in that case the JSON-decoded text is preferred.
    """
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    if isinstance(text, bytes):
        try:
            return text.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if isinstance(text, dict):
        for key in ("data", "text", "content", "value", "message"):
            value = text.get(key)
            if isinstance(value, str):
                return value
        try:
            return json.dumps(text, ensure_ascii=False)
        except Exception:
            return ""
    return str(text)


def _gg_coder_prompt_text(*, system_prompt: str, user_prompt: str) -> str:
    """Compose the prompt GG Coder receives.

    GG Coder has its own system prompt; we do not pass an override unless the
    caller asked for one, otherwise the upstream behaviour stays intact. The
    user prompt is forwarded verbatim.
    """
    del system_prompt
    return user_prompt


async def _run_gg_coder_provider(
    *,
    model_name: str,
    prompt_text: str,
    session_id: str | None,
    mcp: dict[str, Any],
    event_sink: Callable[[str, Any], Awaitable[None]] | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    command = provider_command(
        harness_kind="GG_CODER",
        model_name=model_name,
        prompt_text=prompt_text,
        mcp=mcp,
    )
    if not command:
        raise RuntimeError("No provider command configured for GG_CODER")
    template = provider_command_template("GG_CODER")
    prompt_is_argument = "{prompt}" in template
    cwd = provider_cwd_for_run("GG_CODER", mcp)
    write_provider_mcp_files("GG_CODER", cwd, mcp)
    env = provider_environment(harness_kind="GG_CODER", mcp=mcp)
    resume_path = _gg_coder_resume_token(session_id)
    # ``provider_command()`` returns the argv produced by the harness template
    # (e.g. ``ggcoder --json --provider anthropic --model {model} --max-turns 25``).
    # Insert ``--resume <path>`` after the binary so the binary sees a known
    # global flag before any subcommand-only options. We never append when
    # session_id is missing or the id is opaque (no file path), so the
    # default behaviour stays "start a new upstream session".
    if resume_path:
        command = [command[0], "--resume", resume_path, *command[1:]]
    daemon_log(
        "start ggcoder provider",
        {"harness_kind": "GG_CODER", "command": command, "cwd": str(cwd), "model_name": model_name},
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=None if prompt_is_argument else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd),
        env=env,
        limit=STREAM_READER_LIMIT,
    )
    stdout_parts: list[str] = []
    raw_stdout_parts: list[str] = []
    stderr_task = asyncio.create_task(drain_stream(process.stderr))
    # Emit per-chunk tokens like the upstream codex harness: the gogett chat
    # UI aggregates them into one assistant bubble via useTranscriptEvents.
    state = StreamTextState(harness_kind="GG_CODER", event_sink=event_sink)
    emitted_session_id: str | None = None
    try:
        if not prompt_is_argument:
            assert process.stdin is not None
            try:
                process.stdin.write(prompt_text.encode())
                await process.stdin.drain()
                process.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                # ggcoder may have already exited (e.g. provider auth failure); we'll
                # surface the stderr/stdout when we read the process below.
                pass
        async with asyncio.timeout(daemon_turn_timeout_seconds()):
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
                stream_session_id = str(event.get("session_id") or "") or None
                if (
                    stream_session_id
                    and stream_session_id != session_id
                    and stream_session_id != emitted_session_id
                    and event_sink is not None
                ):
                    emitted_session_id = stream_session_id
                    await event_sink(
                        "status",
                        _daemon_session_started_payload(
                            harness_kind="GG_CODER", session_id=stream_session_id
                        ),
                    )
                handled_text = await _handle_gg_coder_event(event, state)
                if handled_text:
                    stdout_parts.append(handled_text)
            await process.wait()
            stderr_text = await stderr_task
    except TimeoutError as exc:
        if isinstance(exc, GgCoderMaxTurnsReached):
            # Surface our ``max_turns`` info bubble verbatim -- don't replace
            # it with the generic provider-turn-timed-out string.
            await terminate_gracefully(process)
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
            raise
        await terminate_gracefully(process)
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
        raise TimeoutError("GG_CODER provider turn timed out")
    except asyncio.CancelledError:
        await terminate_gracefully(process)
        stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task
        raise
    await state.flush(is_final=True)
    raw_stdout = "".join(raw_stdout_parts).strip()
    stdout = state.full_text.strip() or "".join(stdout_parts).strip() or raw_stdout
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": int(process.returncode or 0),
        "stdout": stdout,
        "stderr": (stderr_text or "").strip(),
        "streamed_tokens": state.streamed_tokens,
        "streamed_messages": state.streamed_messages,
    }


async def _handle_gg_coder_event(event: dict[str, Any], state: StreamTextState) -> str:
    """Translate a single NDJSON event from ``ggcoder --json`` into Lemma events.

    Schema (see KenKaiii/gg-framework: packages/ggcoder/src/modes/json-mode.ts):
      { "type": "text_delta",       "text": "..." }
      { "type": "thinking_delta",   "text": "..." }
      { "type": "tool_call_start",  "toolCallId", "name", "args" }
      { "type": "tool_call_end",    "toolCallId", "name", "isError", "durationMs", "result" }
      { "type": "turn_end",         "turn", "usage" }
      { "type": "agent_done",       "totalTurns", "totalUsage" }
      { "type": "error",            "message" }
    """
    event_type = str(event.get("type") or "")
    if event_type == "text_delta":
        # Emit per-chunk via state.update_text_snapshot (matches upstream codex
        # harness); the chat surface aggregates them into one bubble. Defense
        # in depth: if upstream ever emits a JSON-shaped string (e.g. nested
        # object rather than a string slice), strip the envelope and surface
        # only the readable text -- so the chat bubble can never render raw
        # NDJSON even if the upstream binary misbehaves.
        raw_text = event.get("text")
        text = _strip_text_envelope(raw_text)
        return await state.update_text_snapshot(text)
    if event_type == "thinking_delta":
        return ""
    if event_type == "tool_call_start":
        # Emit a chat-friendly tool_call message so the chat SDK's tool-card
        # rendering kicks in. We previously early-returned here, which made
        # tool activity invisible in the chat bubble.
        await _emit_gg_coder_tool_event(event, state, kind="tool_call")
        return ""
    if event_type == "tool_call_update":
        # Long tool calls (e.g. AgentBox sandboxes) emit progress frames;
        # surface them as inline text so users see movement instead of a
        # frozen spinner until ``tool_call_end`` arrives.
        update = event.get("update")
        if isinstance(update, str) and update:
            return await state.update_text_snapshot(update)
        return ""
    if event_type == "tool_call_end":
        # Emit a tool_return message; chat SDK uses this to mark the tool
        # card as completed (success or error).
        await _emit_gg_coder_tool_event(event, state, kind="tool_return")
        return ""
    if event_type == "server_tool_call":
        await _emit_gg_coder_server_tool_event(event, state, phase="call")
        return ""
    if event_type == "server_tool_result":
        await _emit_gg_coder_server_tool_event(event, state, phase="result")
        return ""
    if event_type == "max_turns":
        await state.flush(is_final=True)
        message = (
            f"ggcoder hit the configured --max-turns cap "
            f"({event.get('maxTurns', '?')}) and stopped."
        )
        if state.event_sink is not None:
            await state.event_sink(
                "message",
                {
                    "role": "assistant",
                    "kind": "info",
                    "text": message,
                    "metadata": {
                        "harness_kind": state.harness_kind,
                        "provider": "ggcoder",
                    },
                },
            )
        raise GgCoderMaxTurnsReached(message)
    if event_type == "agent_done":
        # Flush accumulated text into a single consolidated MESSAGE event.
        # Final flush: emit a single consolidated MESSAGE event so the
        # chat surface renders one assistant bubble even if upstream
        # aggregation missed any chunks.
        await state.flush(is_final=True)
        return ""
    if event_type == "turn_end":
        return ""
    if event_type == "error":
        message = str(event.get("message") or "ggcoder error")
        if state.event_sink is not None:
            await state.event_sink(
                "message",
                {
                    "role": "assistant",
                    "kind": "error",
                    "text": message,
                    "metadata": {"harness_kind": state.harness_kind, "provider": "ggcoder"},
                },
            )
        return message
    return ""


async def _emit_gg_coder_server_tool_event(
    event: dict[str, Any],
    state: StreamTextState,
    *,
    phase: str,
) -> None:
    """Surface ``server_tool_call`` / ``server_tool_result`` events to chat.

    GG Coder emits these for provider-side tools (Anthropic ``web_search`` /
    ``code_execution``, OpenAI ``file_search``, etc.) which never show up as
    ``tool_call_start`` / ``tool_call_end`` events, so without this bridge the
    chat surface has no record the server did anything between text deltas.
    """
    if phase == "call":
        tool_call_id = str(event.get("id") or event.get("toolUseId") or "")
        tool_name = str(event.get("name") or "")
        if not tool_call_id or not tool_name:
            return
        if tool_call_id in state.emitted_tool_call_ids:
            return
        state.emitted_tool_call_ids.add(tool_call_id)
        await state.flush(is_final=False)
        message = {
            "role": "assistant",
            "kind": "tool_call",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tool_args": event.get("input") or {},
            "metadata": {
                "tool_name": tool_name,
                "provider": "ggcoder",
                "server_tool": True,
            },
        }
        state.streamed_tokens = True
        if state.event_sink is not None:
            await state.event_sink("token", codex_tool_token(message))
            await state.event_sink("message", message)
        return
    tool_call_id = str(
        event.get("toolUseId") or event.get("tool_call_id") or ""
    )
    if not tool_call_id or tool_call_id in state.emitted_tool_return_ids:
        return
    state.emitted_tool_return_ids.add(tool_call_id)
    if state.event_sink is not None:
        await state.event_sink(
            "message",
            {
                "role": "tool",
                "kind": "tool_return",
                "tool_name": str(event.get("name") or ""),
                "tool_call_id": tool_call_id,
                "tool_result": event.get("data"),
                "metadata": {
                    "provider": "ggcoder",
                    "server_tool": True,
                    "result_type": str(event.get("resultType") or ""),
                },
            },
        )


async def _emit_gg_coder_tool_event(
    event: dict[str, Any],
    state: StreamTextState,
    *,
    kind: str,
) -> None:
    tool_call_id = str(event.get("toolCallId") or event.get("tool_call_id") or "")
    tool_name = str(event.get("name") or event.get("tool") or "")
    if not tool_call_id or not tool_name:
        return
    if kind == "tool_call":
        if tool_call_id in state.emitted_tool_call_ids:
            return
        state.emitted_tool_call_ids.add(tool_call_id)
        await state.flush(is_final=False)
        tool_args = event.get("args") or event.get("input") or {}
        if state.event_sink is not None:
            message = {
                "role": "assistant",
                "kind": "tool_call",
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "tool_args": tool_args,
                "metadata": {"tool_name": tool_name, "provider": "ggcoder"},
            }
            state.streamed_tokens = True
            # Per-chunk token so the chat UI aggregates; final message too
            # so tool cards render even when streaming is disabled.
            await state.event_sink("token", codex_tool_token(message))
            await state.event_sink("message", message)
        return
    if kind == "tool_return":
        if tool_call_id in state.emitted_tool_return_ids:
            return
        state.emitted_tool_return_ids.add(tool_call_id)
        # ``ggcoder --json`` carries the result on the same event as the
        # completion marker; pull it once so both branches (success/error)
        # surface a single value to the chat surface.
        if event.get("isError") or event.get("is_error"):
            result = event.get("error") or event.get("result")
        else:
            result = event.get("result")
        if state.event_sink is not None:
            await state.event_sink(
                "message",
                {
                    "role": "tool",
                    "kind": "tool_return",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "tool_result": result,
                    "metadata": {
                        "tool_name": tool_name,
                        "provider": "ggcoder",
                        "is_error": bool(event.get("isError") or event.get("is_error")),
                    },
                },
            )


def validate_model_name(value: str) -> str:
    """Public helper used by CLI argparse -- mirrors the relaxed ggcoder rules."""
    candidate = value.strip()
    if not _MODEL_HINT.match(candidate):
        raise ValueError(
            f"Invalid model name {value!r}. Allowed: letters, digits, ., _, :, /, - (max 80 chars)."
        )
    return candidate
