from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import socket
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from .base import StreamTextState
from .codex import (
    _daemon_session_invalid_payload,
    _daemon_session_started_payload,
    daemon_turn_timeout_seconds,
)
from .._logging import log as daemon_log, preview as _preview
from ..mcp import (
    looks_like_lemma_mcp_payload,
    merged_opencode_config,
    provider_command,
    provider_cwd_for_run,
    provider_environment,
)
from ..process import STREAM_READER_LIMIT


def _opencode_debug_logs_enabled() -> bool:
    from .._logging import is_debug
    raw = os.getenv("LEMMA_DAEMON_OPENCODE_DEBUG_LOGS")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return is_debug()


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class OpenCodeHarness:
    kind = "OPENCODE"

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
        prompt_text = _build_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)
        return await _run_opencode_server_provider(
            model_name=model_name,
            prompt_text=prompt_text,
            session_id=session_id,
            mcp=mcp,
            event_sink=event_sink,
            stop_event=stop_event,
        )

    async def close(self) -> None:
        pass


async def _run_opencode_server_provider(
    *,
    model_name: str,
    prompt_text: str,
    session_id: str | None = None,
    mcp: dict[str, Any],
    event_sink: Callable[[str, Any], Awaitable[None]] | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    import contextlib

    cwd = provider_cwd_for_run("OPENCODE", mcp)
    binary = provider_command(
        harness_kind="OPENCODE",
        model_name=model_name,
        prompt_text=prompt_text,
        mcp=mcp,
    )[0]
    port = _free_tcp_port()
    env = provider_environment(harness_kind="OPENCODE", mcp=mcp)
    command = [binary, "serve", "--port", str(port), "--hostname", "127.0.0.1"]
    if _opencode_debug_logs_enabled():
        command[2:2] = ["--print-logs", "--log-level", "DEBUG"]
    daemon_log(
        "opencode server provider start",
        {
            "binary": binary,
            "command": command,
            "port": port,
            "cwd": str(cwd),
            "model_name": model_name,
            "opencode_config": env.get("OPENCODE_CONFIG_CONTENT"),
        },
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STREAM_READER_LIMIT,
    )
    server_output: list[str] = []
    log_tasks: list[asyncio.Task[None]] = []
    try:
        base_url, startup_lines = await _read_opencode_server_url(process, timeout_seconds=15)
        server_output.extend(startup_lines)
        log_tasks = [
            asyncio.create_task(_drain_opencode_server_stream(stream, server_output))
            for stream in (process.stdout, process.stderr)
            if stream is not None
        ]
        daemon_log("opencode server ready", {"base_url": base_url})
        output = await _run_opencode_turn(
            base_url=base_url,
            cwd=cwd,
            model_name=model_name,
            prompt_text=prompt_text,
            session_id=session_id,
            mcp=mcp,
            event_sink=event_sink,
            server_output=server_output,
            stop_event=stop_event,
        )
        return {
            "command": command,
            "cwd": str(cwd),
            "returncode": 0,
            "stdout": output.strip(),
            "stderr": "",
            "streamed_tokens": event_sink is not None,
            "streamed_messages": event_sink is not None and bool(output.strip()),
        }
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        for task in log_tasks:
            task.cancel()
        for task in log_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _run_opencode_turn(
    *,
    base_url: str,
    cwd: Any,
    model_name: str,
    prompt_text: str,
    session_id: str | None,
    mcp: dict[str, Any],
    event_sink: Callable[[str, Any], Awaitable[None]] | None = None,
    server_output: list[str] | None = None,
    stop_event: asyncio.Event | None = None,
) -> str:
    import httpx

    params = {"directory": str(cwd)}
    config = merged_opencode_config(os.environ.get("OPENCODE_CONFIG_CONTENT"), mcp)
    mcp_server_names = tuple((config.get("mcp") or {}).keys())
    async with httpx.AsyncClient(timeout=None) as client:
        for server_name, server_config in (config.get("mcp") or {}).items():
            if isinstance(server_config, dict) and server_config.get("enabled", True) is not False:
                daemon_log("opencode connect mcp", {"server_name": server_name, "config": server_config})
                await _opencode_request(client, "POST", base_url, f"/mcp/{server_name}/connect", params=params)
        original_session_id = session_id
        is_new_session = session_id is None
        if is_new_session:
            session = await _opencode_request(
                client, "POST", base_url, "/session", params=params, body={"title": "Lemma OpenCode"},
            )
            session_id = str(session.get("id") if isinstance(session, dict) else "")
            daemon_log("opencode session created", {"session": session, "session_id": session_id})
        if not session_id:
            raise RuntimeError("OpenCode did not return a session id")
        if is_new_session and event_sink is not None:
            await event_sink("status", _daemon_session_started_payload(harness_kind="OPENCODE", session_id=session_id))
        # Seed the baseline with assistant text already in the resumed session
        # (captured BEFORE submitting this turn's prompt) so a continuation turn
        # does not re-surface the prior turn's reply as this turn's output. A
        # freshly created session has no baseline.
        baseline_output = ""
        if not is_new_session:
            with contextlib.suppress(RuntimeError):
                existing = await _opencode_request(
                    client, "GET", base_url, f"/session/{session_id}/message", params=params
                )
                if isinstance(existing, list):
                    baseline_output = _opencode_latest_assistant_text(existing)
        body: dict[str, Any] = {"parts": [{"type": "text", "text": prompt_text}]}
        model = _opencode_model_payload(model_name)
        if model:
            body["model"] = model
        try:
            await _opencode_request(client, "POST", base_url, f"/session/{session_id}/prompt_async", params=params, body=body)
        except RuntimeError as exc:
            if original_session_id is None or not _opencode_saved_session_error_is_recoverable(exc):
                raise
            if event_sink is not None:
                await event_sink("status", _daemon_session_invalid_payload(harness_kind="OPENCODE", session_id=original_session_id))
            session = await _opencode_request(client, "POST", base_url, "/session", params=params, body={"title": "Lemma OpenCode"})
            session_id = str(session.get("id") if isinstance(session, dict) else "")
            if not session_id:
                raise RuntimeError("OpenCode did not return a replacement session id") from exc
            baseline_output = ""  # recovered session is fresh; no prior context
            if event_sink is not None:
                await event_sink("status", _daemon_session_started_payload(harness_kind="OPENCODE", session_id=session_id))
            await _opencode_request(client, "POST", base_url, f"/session/{session_id}/prompt_async", params=params, body=body)
        daemon_log("opencode prompt submitted", {"session_id": session_id, "body": body})
        # OpenCode reports generation failures (unknown model, provider auth,
        # rate limits) ONLY on its /event SSE stream -- not in the message list,
        # the session object, or the server's stdout. Tail that stream so we can
        # surface the real reason instead of a generic "no output" error.
        session_errors: list[str] = []
        events_task = asyncio.create_task(
            _consume_opencode_session_errors(client, base_url, params, session_id, session_errors)
        )
        try:
            deadline = asyncio.get_running_loop().time() + daemon_turn_timeout_seconds()
            startup_grace = float(os.getenv("LEMMA_DAEMON_OPENCODE_STARTUP_GRACE_SECONDS", "10"))
            turn_started_at = asyncio.get_running_loop().time()
            last_output = baseline_output
            missing_status_since: float | None = None
            text_state = StreamTextState(harness_kind="OPENCODE", event_sink=event_sink)
            while asyncio.get_running_loop().time() < deadline:
                if stop_event is not None and stop_event.is_set():
                    break
                now = asyncio.get_running_loop().time()
                # A reported session error means generation failed; surface it
                # right away rather than waiting out the grace period to raise a
                # generic message. Prefer any partial output if we already have it.
                if session_errors:
                    await text_state.flush(is_final=True)
                    if last_output != baseline_output:
                        return last_output
                    raise RuntimeError(f"OpenCode error: {_join_opencode_errors(session_errors)}")
                await _accept_lemma_opencode_permissions(client, base_url, params=params)
                messages = await _opencode_request(client, "GET", base_url, f"/session/{session_id}/message", params=params)
                if isinstance(messages, list):
                    await text_state.update_tool_parts(
                        _opencode_tool_parts(messages, mcp_server_names)
                    )
                    text = _opencode_latest_assistant_text(messages)
                    # Only treat text that differs from the pre-turn baseline as this
                    # turn's output.
                    if text and text != baseline_output:
                        if text != last_output:
                            daemon_log("opencode latest assistant text", _preview(text))
                            await text_state.update_text_snapshot(text)
                        last_output = text
                saw_new_output = last_output != baseline_output
                status = await _opencode_request(client, "GET", base_url, "/session/status", params=params)
                session_status = status.get(session_id) if isinstance(status, dict) else None
                daemon_log("opencode session status", {"session_id": session_id, "status": session_status})
                if isinstance(status, dict) and session_id not in status and saw_new_output:
                    await text_state.flush(is_final=True)
                    return last_output
                if isinstance(status, dict) and session_id not in status:
                    if missing_status_since is None:
                        missing_status_since = now
                    if now - missing_status_since < startup_grace:
                        await asyncio.sleep(0.5)
                        continue
                    await text_state.flush(is_final=True)
                    raise RuntimeError(_opencode_no_output_error(session_errors, server_output))
                missing_status_since = None
                if isinstance(session_status, dict) and session_status.get("type") != "busy":
                    # A resumed session can briefly report idle before it begins
                    # processing the new prompt; don't conclude the turn until we've
                    # captured new output or the startup grace has elapsed.
                    if saw_new_output or (now - turn_started_at) >= startup_grace:
                        await text_state.flush(is_final=True)
                        return last_output if saw_new_output else ""
                await asyncio.sleep(0.5)
            raise TimeoutError("OpenCode server turn timed out")
        finally:
            events_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await events_task


_OPENCODE_GET_RETRY_ATTEMPTS = 3
_OPENCODE_GET_RETRY_BASE_DELAY = 0.5


async def _opencode_request(
    client: Any,
    method: str,
    base_url: str,
    path: str,
    *,
    params: dict[str, str],
    body: dict[str, Any] | None = None,
) -> Any:
    url = f"{base_url.rstrip('/')}{path}?{urlencode(params)}"
    # Only GETs are safe to retry automatically — they poll status/messages and are
    # idempotent. POSTs (session create, prompt) are left to the caller's saved-
    # session recovery so a transient blip never double-submits a prompt.
    retryable = method.upper() == "GET"
    attempts = _OPENCODE_GET_RETRY_ATTEMPTS if retryable else 1
    for attempt in range(attempts):
        last = attempt == attempts - 1
        daemon_log(
            "opencode http request",
            {"method": method, "path": path, "body": body, "attempt": attempt},
        )
        try:
            response = await client.request(method, url, json=body)
        except Exception as exc:  # noqa: BLE001 - transport error (reset/timeout)
            if not retryable or last:
                raise
            daemon_log(
                "opencode http transport error; retrying",
                {"path": path, "error": str(exc)},
            )
            await asyncio.sleep(_OPENCODE_GET_RETRY_BASE_DELAY * (2 ** attempt))
            continue
        daemon_log(
            "opencode http response",
            {"method": method, "path": path, "status_code": response.status_code, "text_preview": _preview(response.text)},
        )
        if response.status_code >= 500 and retryable and not last:
            daemon_log(
                "opencode http 5xx; retrying",
                {"path": path, "status_code": response.status_code},
            )
            await asyncio.sleep(_OPENCODE_GET_RETRY_BASE_DELAY * (2 ** attempt))
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"OpenCode {method} {path} failed: {response.text}")
        if not response.text:
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text
    # Unreachable: the loop returns or raises on the final attempt.
    raise RuntimeError(f"OpenCode {method} {path} failed after {attempts} attempts")


async def _accept_lemma_opencode_permissions(
    client: Any,
    base_url: str,
    *,
    params: dict[str, str],
) -> None:
    permissions = await _opencode_request(client, "GET", base_url, "/permission", params=params)
    if not isinstance(permissions, list):
        return
    for permission in permissions:
        if not isinstance(permission, dict):
            continue
        request_id = str(permission.get("id") or "")
        if not request_id:
            continue
        reply = "once" if looks_like_lemma_mcp_payload(permission) else "reject"
        daemon_log("opencode permission reply", {"permission": permission, "reply": reply})
        await _opencode_request(
            client, "POST", base_url, f"/permission/{request_id}/reply", params=params, body={"reply": reply},
        )


async def _consume_opencode_session_errors(
    client: Any,
    base_url: str,
    params: dict[str, str],
    session_id: str,
    sink: list[str],
) -> None:
    """Collect ``session.error`` messages from OpenCode's ``/event`` SSE stream.

    OpenCode surfaces model/provider/runtime failures only here, so tailing the
    stream lets the turn report the real reason (e.g. "Model not found", provider
    auth, rate limit). Best-effort: any failure reading the stream is swallowed
    so it can never break the turn itself.
    """
    try:
        async with client.stream("GET", f"{base_url}/event", params=params) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[len("data:"):].strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "session.error":
                    continue
                props = event.get("properties") if isinstance(event.get("properties"), dict) else {}
                event_session = str(props.get("sessionID") or "")
                if event_session and session_id and event_session != session_id:
                    continue
                error = props.get("error") if isinstance(props.get("error"), dict) else {}
                data = error.get("data") if isinstance(error.get("data"), dict) else {}
                message = data.get("message") or error.get("name") or "OpenCode session error"
                sink.append(str(message))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- diagnostics stream is best-effort
        pass


def _join_opencode_errors(session_errors: list[str]) -> str:
    """De-duplicated, order-preserving join of captured session errors."""
    return "; ".join(dict.fromkeys(err for err in session_errors if err))


def _opencode_no_output_error(session_errors: list[str], server_output: list[str] | None) -> str:
    """Build the most informative message for a turn that produced no output."""
    joined = _join_opencode_errors(session_errors)
    if joined:
        return f"OpenCode session ended without assistant output: {joined}"
    stderr_tail = "\n".join((server_output or [])[-80:]).strip()
    return (
        "OpenCode session ended without assistant output"
        + (f":\n{stderr_tail}" if stderr_tail else "")
    )


def _opencode_latest_assistant_text(messages: list[Any]) -> str:
    output = ""
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
        role = str(info.get("role") or entry.get("role") or "")
        if role and role != "assistant":
            continue
        parts = entry.get("parts")
        if not isinstance(parts, list):
            content = info.get("content")
            parts = content if isinstance(content, list) else []
        text = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict) and str(part.get("type") or "") == "text"
        )
        if text:
            output = text
    return output


def _strip_mcp_server_prefix(tool_name: str, server_names: tuple[str, ...]) -> str:
    """Strip opencode's MCP server-name prefix from a tool name.

    opencode exposes an MCP server's tools as ``<server>_<tool>`` (e.g. the
    ``lemma_tools`` server's ``lemma_exec_command`` becomes
    ``lemma_tools_lemma_exec_command``). Strip the server prefix so the emitted
    tool_name is the canonical MCP tool name the rest of Lemma uses.
    """
    for server in server_names:
        prefix = f"{server}_"
        if server and tool_name.startswith(prefix):
            return tool_name[len(prefix):]
    return tool_name


def _opencode_tool_parts(
    messages: list[Any], server_names: tuple[str, ...] = ()
) -> list[dict]:
    """Return assistant ToolParts (``type == "tool"``) from opencode messages.

    opencode's message endpoint returns each message's structured ``parts``; a tool
    part carries ``callID``, ``tool`` (name) and ``state`` (status + input/output).
    ``_opencode_latest_assistant_text`` drops these; we surface them so opencode
    tool calls/outputs reach the conversation as TOOL_CALL/TOOL_RETURN messages,
    like the codex and claude_code harnesses. The ``tool`` field is normalized to
    the canonical MCP tool name (opencode prefixes it with the MCP server name).
    """
    parts_out: list[dict] = []
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        info = entry.get("info") if isinstance(entry.get("info"), dict) else entry
        role = str(info.get("role") or entry.get("role") or "")
        if role and role != "assistant":
            continue
        parts = entry.get("parts")
        if not isinstance(parts, list):
            content = info.get("content")
            parts = content if isinstance(content, list) else []
        for part in parts:
            if isinstance(part, dict) and str(part.get("type") or "") == "tool":
                normalized = dict(part)
                normalized["tool"] = _strip_mcp_server_prefix(
                    str(part.get("tool") or ""), server_names
                )
                parts_out.append(normalized)
    return parts_out


def _opencode_model_payload(model_name: str) -> dict[str, str] | None:
    if not model_name or model_name.lower() == "default" or "/" not in model_name:
        return None
    provider, model = model_name.split("/", 1)
    if not provider or not model:
        return None
    return {"providerID": provider, "modelID": model}


def _opencode_saved_session_error_is_recoverable(exc: RuntimeError) -> bool:
    detail = str(exc).lower()
    return "/session/" in detail and any(
        marker in detail
        for marker in ("404", "not found", "missing", "expired", "invalid")
    )


async def _read_opencode_server_url(
    process: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
) -> tuple[str, list[str]]:
    pattern = re.compile(r"opencode server listening on (https?://\S+)")
    streams = [process.stdout, process.stderr]
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    lines: list[str] = []
    while asyncio.get_running_loop().time() < deadline:
        if process.returncode is not None:
            break
        for stream in streams:
            if stream is None:
                continue
            try:
                line = await asyncio.wait_for(stream.readline(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if not line:
                continue
            text = line.decode(errors="replace")
            daemon_log("opencode server output", text.strip())
            lines.append(text)
            match = pattern.search(text.strip())
            if match:
                return match.group(1), lines
    raise RuntimeError("".join(lines).strip() or "OpenCode server did not print a listening URL")


async def _drain_opencode_server_stream(
    stream: asyncio.StreamReader | None,
    lines: list[str],
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode(errors="replace")
        daemon_log("opencode server output", text.strip())
        lines.append(text.strip())
        del lines[:-200]


def _build_prompt_text(*, system_prompt: str, user_prompt: str) -> str:
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
