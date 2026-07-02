from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from .base import emit_assistant_text_message
from .._logging import log as daemon_log, preview as _preview
from .._utils import (
    bounded_error_detail,
    jsonrpc_error_detail,
    parse_jsonish,
    strip_prompt_echo_from_text,
)
from ..mcp import (
    mcp_conversation_id,
    provider_command,
    provider_cwd_for_run,
    provider_environment,
)
from ..process import STREAM_READER_LIMIT

CODEX_WORKER_TTL_SECONDS_ENV = "LEMMA_DAEMON_CODEX_WORKER_TTL_SECONDS"
DAEMON_TURN_TIMEOUT_SECONDS_ENV = "LEMMA_DAEMON_TURN_TIMEOUT_SECONDS"
DEFAULT_DAEMON_TURN_TIMEOUT_SECONDS = 7200.0
DEFAULT_CODEX_WORKER_TTL_SECONDS = DEFAULT_DAEMON_TURN_TIMEOUT_SECONDS
# Bounds how many distinct conversations can have a live codex app-server
# subprocess at once. Unlike the daemon-wide run cap (runner.py's
# max_concurrent_runs, admission-control/reject), this one QUEUES a new
# conversation's first turn when full rather than rejecting it: an unrelated
# stale-but-still-warm conversation shouldn't fail a brand new one on a
# single-user laptop with no peers to redistribute to, and the daemon-wide cap
# already bounds total exposure.
CODEX_POOL_MAX_WORKERS_ENV = "LEMMA_DAEMON_CODEX_POOL_MAX_WORKERS"
DEFAULT_CODEX_POOL_MAX_WORKERS = 4


def codex_pool_max_workers() -> int:
    raw = os.getenv(CODEX_POOL_MAX_WORKERS_ENV, str(DEFAULT_CODEX_POOL_MAX_WORKERS))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_CODEX_POOL_MAX_WORKERS


class JsonRpcRequestError(RuntimeError):
    def __init__(self, *, method: str, error: object, stderr_tail: str = "") -> None:
        self.method = method
        self.error = error
        self.stderr_tail = stderr_tail
        super().__init__(
            bounded_error_detail(
                jsonrpc_error_detail(method=method, error=error, stderr_tail=stderr_tail)
            )
        )


class JsonRpcProcess:
    """Manages a subprocess that speaks JSON-RPC over stdin/stdout."""

    def __init__(self, command: list[str], *, cwd: Path, env: dict[str, str]):
        self.command = command
        self.cwd = cwd
        self.env = env
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.server_requests: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.stderr_lines: list[str] = []
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        daemon_log("jsonrpc process start", {"command": self.command, "cwd": str(self.cwd)})
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
            env=self.env,
            limit=STREAM_READER_LIMIT,
        )
        self._tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
        ]

    async def close(self) -> None:
        if self.process is not None:
            if self.process.stdin is not None and not self.process.stdin.is_closing():
                self.process.stdin.close()
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
                    await self.process.wait()
        for task in self._tasks:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        daemon_log("jsonrpc request", {"id": request_id, "method": method, "params": params or {}})
        await self._send({"id": request_id, "method": method, "params": params or {}})
        message = await asyncio.wait_for(future, timeout=timeout)
        daemon_log("jsonrpc response", {"id": request_id, "message": message})
        if "error" in message:
            raise JsonRpcRequestError(
                method=method,
                error=message["error"],
                stderr_tail="\n".join(self.stderr_lines[-20:]),
            )
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        daemon_log("jsonrpc notify", {"method": method, "params": params or {}})
        await self._send({"method": method, "params": params or {}})

    async def respond(self, request_id: object, result: dict[str, Any]) -> None:
        daemon_log("jsonrpc respond", {"id": request_id, "result": result})
        await self._send({"id": request_id, "result": result})

    async def respond_error(self, request_id: object, message: str) -> None:
        daemon_log("jsonrpc respond error", {"id": request_id, "message": message})
        await self._send({"id": request_id, "error": {"code": -32601, "message": message}})

    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.process is None:
            await self.start()
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while True:
            line = await self.process.stdout.readline()
            if not line:
                return
            try:
                message = json.loads(line.decode(errors="replace"))
            except json.JSONDecodeError:
                daemon_log("jsonrpc non-json stdout", _preview(line.decode(errors="replace")))
                continue
            if isinstance(message, dict):
                daemon_log("jsonrpc incoming", message)
                self._dispatch(message)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            stderr_line = line.decode(errors="replace").rstrip()
            daemon_log("jsonrpc stderr", stderr_line)
            self.stderr_lines.append(stderr_line)
            self.stderr_lines = self.stderr_lines[-200:]

    def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is not None and ("result" in message or "error" in message):
            pending = self._pending.pop(int(request_id), None)
            if pending is not None and not pending.done():
                pending.set_result(message)
            return
        if request_id is not None and "method" in message:
            self.server_requests.put_nowait(message)
            return
        if "method" in message:
            self.notifications.put_nowait(message)


class CodexWorker:
    """Manages a single Codex app-server process per conversation."""

    def __init__(self, *, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        self.client: JsonRpcProcess | None = None
        self.command: list[str] = []
        self.cwd: Path | None = None
        self.mcp_url: str | None = None
        self.last_used_at = time.monotonic()
        self._lock = asyncio.Lock()

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
        async with self._lock:
            try:
                await self._ensure_started(mcp=mcp, model_name=model_name, prompt_text=user_prompt)
                assert self.client is not None
                _drain_queue(self.client.notifications)
                _drain_queue(self.client.server_requests)
                try:
                    return await _run_codex_app_server_turn(
                        client=self.client,
                        command=self.command,
                        cwd=self.cwd or provider_cwd_for_run("CODEX", mcp),
                        model_name=model_name,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        session_id=session_id,
                        event_sink=event_sink,
                        stop_event=stop_event,
                    )
                except asyncio.CancelledError:
                    daemon_log(
                        "codex app-server worker cancelled; closing process",
                        {"conversation_id": self.conversation_id},
                    )
                    await self.close()
                    raise
            finally:
                self.last_used_at = time.monotonic()

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None
        self.mcp_url = None

    async def _ensure_started(
        self,
        *,
        mcp: dict[str, Any],
        model_name: str,
        prompt_text: str,
    ) -> None:
        from ..mcp import mcp_url as get_mcp_url
        mcp_url_val = get_mcp_url(mcp)
        cwd = provider_cwd_for_run("CODEX", mcp)
        if (
            self.client is not None
            and self.client.is_alive()
            and self.mcp_url == mcp_url_val
            and self.cwd == cwd
        ):
            return
        await self.close()
        command = provider_command(
            harness_kind="CODEX",
            model_name=model_name,
            prompt_text=prompt_text,
            mcp=mcp,
        )
        if not command:
            raise RuntimeError("No provider command configured for CODEX")
        env = provider_environment(harness_kind="CODEX", mcp=mcp)
        daemon_log(
            "codex app-server warm worker start",
            {"conversation_id": self.conversation_id, "command": command, "cwd": str(cwd), "mcp_url": mcp_url_val},
        )
        client = JsonRpcProcess(command, cwd=cwd, env=env)
        await client.start()
        await client.request(
            "initialize",
            {
                "clientInfo": {"name": "lemma-daemon", "title": "Lemma User Daemon", "version": "0.1.0"},
                "capabilities": {},
            },
            timeout=10,
        )
        await client.notify("initialized")
        self.client = client
        self.command = command
        self.cwd = cwd
        self.mcp_url = mcp_url_val


class CodexWorkerPool:
    """Manages a pool of CodexWorkers with TTL-based idle cleanup.

    Bounded by ``codex_pool_max_workers()``: acquiring a semaphore slot is
    required only to *create* a new worker, never to reuse an existing one for
    an already-pooled conversation, so a single conversation's turns never
    block on the pool being "full" of itself.
    """

    def __init__(self) -> None:
        self._workers: dict[str, CodexWorker] = {}
        self._ttl_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(codex_pool_max_workers())

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
        conversation_id = mcp_conversation_id(mcp)
        worker = await self._worker_for_conversation(conversation_id)
        try:
            result = await worker.run(
                model_name=model_name,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                session_id=session_id,
                mcp=mcp,
                event_sink=event_sink,
                stop_event=stop_event,
            )
        except asyncio.CancelledError:
            await self._retire_worker(conversation_id, worker)
            raise
        except Exception:
            await self._retire_worker(conversation_id, worker)
            raise
        self._schedule_idle_close(conversation_id, worker)
        return result

    async def close(self) -> None:
        workers = list(self._workers.values())
        ttl_tasks = list(self._ttl_tasks.values())
        self._workers.clear()
        self._ttl_tasks.clear()
        for task in ttl_tasks:
            task.cancel()
        for task in ttl_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for worker in workers:
            await worker.close()

    async def _worker_for_conversation(self, conversation_id: str) -> CodexWorker:
        async with self._lock:
            task = self._ttl_tasks.pop(conversation_id, None)
            if task is not None:
                task.cancel()
            worker = self._workers.get(conversation_id)
            if worker is not None:
                return worker  # existing conversation -- no new slot needed
        # New conversation: acquire a pool slot. This await is what makes a
        # full pool queue a new conversation's first turn rather than reject
        # it -- it simply waits here until a slot frees.
        await self._slots.acquire()
        async with self._lock:
            worker = self._workers.get(conversation_id)
            if worker is not None:
                # Lost the race to a concurrent call for the same
                # conversation_id while waiting on the semaphore -- release
                # the slot we don't need and reuse the winner's worker.
                self._slots.release()
                return worker
            worker = CodexWorker(conversation_id=conversation_id)
            self._workers[conversation_id] = worker
            return worker

    async def _retire_worker(self, conversation_id: str, worker: CodexWorker) -> None:
        removed = False
        async with self._lock:
            current = self._workers.get(conversation_id)
            if current is worker:
                self._workers.pop(conversation_id, None)
                removed = True
            task = self._ttl_tasks.pop(conversation_id, None)
            if task is not None:
                task.cancel()
        await worker.close()
        if removed:
            self._slots.release()

    def _schedule_idle_close(self, conversation_id: str, worker: CodexWorker) -> None:
        existing = self._ttl_tasks.pop(conversation_id, None)
        if existing is not None:
            existing.cancel()
        self._ttl_tasks[conversation_id] = asyncio.create_task(
            self._close_after_ttl(conversation_id, worker, worker.last_used_at)
        )

    async def _close_after_ttl(
        self,
        conversation_id: str,
        worker: CodexWorker,
        last_used_at: float,
    ) -> None:
        await asyncio.sleep(codex_worker_ttl_seconds())
        async with self._lock:
            current = self._workers.get(conversation_id)
            if current is not worker or worker.last_used_at != last_used_at:
                # Reused since this close was scheduled -- the worker is still
                # live and its slot still legitimately held; do not release.
                return
            self._workers.pop(conversation_id, None)
            self._ttl_tasks.pop(conversation_id, None)
        await worker.close()
        self._slots.release()


_CODEX_APP_SERVER_POOL = CodexWorkerPool()


class CodexHarness:
    kind = "CODEX"

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
        return await _CODEX_APP_SERVER_POOL.run(
            model_name=model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_id=session_id,
            mcp=mcp,
            event_sink=event_sink,
            stop_event=stop_event,
        )

    async def close(self) -> None:
        await _CODEX_APP_SERVER_POOL.close()


def codex_worker_ttl_seconds() -> float:
    raw = os.getenv(CODEX_WORKER_TTL_SECONDS_ENV, str(DEFAULT_CODEX_WORKER_TTL_SECONDS))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_CODEX_WORKER_TTL_SECONDS


def daemon_turn_timeout_seconds() -> float:
    raw = os.getenv(DAEMON_TURN_TIMEOUT_SECONDS_ENV, str(DEFAULT_DAEMON_TURN_TIMEOUT_SECONDS))
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_DAEMON_TURN_TIMEOUT_SECONDS


async def _run_codex_app_server_turn(
    *,
    client: JsonRpcProcess,
    command: list[str],
    cwd: Path,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    session_id: str | None,
    event_sink: Callable[[str, Any], Awaitable[None]] | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, Any]:
    daemon_log(
        "codex app-server turn start",
        {"command": command, "cwd": str(cwd), "model_name": model_name},
    )
    output_parts: list[str] = []
    text_buffer: list[str] = []
    emitted_tool_call_ids: set[str] = set()
    active_tool_call_ids: set[str] = set()
    streamed_messages = False

    async def flush_text_message(*, is_final: bool) -> None:
        nonlocal streamed_messages
        if event_sink is None or not text_buffer:
            return
        text = "".join(text_buffer).strip()
        text_buffer.clear()
        if not text:
            return
        streamed_messages = True
        await emit_assistant_text_message(event_sink, text, harness_kind="CODEX", is_final=is_final)

    async def start_thread() -> str:
        thread_result = await client.request("thread/start", {"cwd": str(cwd)}, timeout=15)
        thread = thread_result.get("thread")
        thread = thread if isinstance(thread, dict) else {}
        created_thread_id = (
            thread.get("id")
            or thread.get("sessionId")
            or thread_result.get("threadId")
            or thread_result.get("sessionId")
        )
        if not created_thread_id:
            raise RuntimeError("codex app-server thread/start returned no thread id")
        if event_sink is not None:
            await event_sink("status", _daemon_session_started_payload(harness_kind="CODEX", session_id=str(created_thread_id)))
        return str(created_thread_id)

    thread_id = session_id
    is_new_thread = thread_id is None
    if is_new_thread:
        thread_id = await start_thread()
    prompt_for_turn = _provider_prompt_text(
        system_prompt=system_prompt if is_new_thread else "",
        user_prompt=user_prompt,
    )
    prompt_echo_candidates = [prompt_for_turn, user_prompt]
    if system_prompt:
        prompt_echo_candidates.append(system_prompt)
    turn_params: dict[str, Any] = {
        "threadId": str(thread_id),
        "input": [{"type": "text", "text": prompt_for_turn}],
    }
    if model_name.lower() != "default":
        turn_params["model"] = model_name
    try:
        await client.request("turn/start", turn_params, timeout=10)
    except JsonRpcRequestError as exc:
        if session_id is not None and _codex_saved_session_error_is_recoverable(exc):
            if event_sink is not None:
                await event_sink("status", _daemon_session_invalid_payload(harness_kind="CODEX", session_id=session_id))
            _drain_queue(client.notifications)
            _drain_queue(client.server_requests)
            thread_id = await start_thread()
            is_new_thread = True
            prompt_for_turn = _provider_prompt_text(system_prompt=system_prompt, user_prompt=user_prompt)
            prompt_echo_candidates = [prompt_for_turn, user_prompt]
            if system_prompt:
                prompt_echo_candidates.append(system_prompt)
            turn_params = {
                "threadId": str(thread_id),
                "input": [{"type": "text", "text": prompt_for_turn}],
            }
            if model_name.lower() != "default":
                turn_params["model"] = model_name
            await client.request("turn/start", turn_params, timeout=10)
        elif session_id is not None:
            raise RuntimeError(
                bounded_error_detail(
                    f"{exc}\n\nCodex rejected the saved local session_id {session_id!r}."
                )
            ) from exc
        else:
            raise
    deadline = asyncio.get_running_loop().time() + daemon_turn_timeout_seconds()
    while asyncio.get_running_loop().time() < deadline:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            request = client.server_requests.get_nowait()
        except asyncio.QueueEmpty:
            request = None
        if request is not None:
            daemon_log("codex server request", request)
            await _handle_codex_server_request(client, request)
            continue
        try:
            note = await asyncio.wait_for(client.notifications.get(), timeout=0.25)
        except asyncio.TimeoutError:
            if not client.is_alive():
                stderr = "\n".join(client.stderr_lines[-20:])
                raise RuntimeError(
                    "codex app-server exited unexpectedly" + (f":\n{stderr}" if stderr else "")
                )
            continue
        delta = codex_text_delta(note)
        if delta:
            delta = strip_prompt_echo_from_text(delta, prompt_candidates=prompt_echo_candidates)
        if delta:
            if _codex_delta_belongs_to_active_tool(note, active_tool_call_ids=active_tool_call_ids, delta=delta):
                daemon_log("codex tool output delta", _preview(delta))
            else:
                daemon_log("codex text delta", _preview(delta))
                output_parts.append(delta)
                text_buffer.append(delta)
                if event_sink is not None:
                    await event_sink("token", {"kind": "text", "data": delta})
        completed_text = codex_new_completed_assistant_text(
            output_parts,
            text_buffer,
            codex_completed_assistant_text(note),
        )
        if completed_text:
            completed_text = strip_prompt_echo_from_text(completed_text, prompt_candidates=prompt_echo_candidates)
        if completed_text:
            daemon_log("codex completed assistant text", _preview(completed_text))
            output_parts.append(completed_text)
            text_buffer.append(completed_text)
            if event_sink is not None:
                await event_sink("token", {"kind": "text", "data": completed_text})
        if event_sink is not None:
            tool_call = codex_tool_call_event(note)
            if tool_call is not None:
                tool_call_id = str(tool_call["tool_call_id"])
                active_tool_call_ids.add(tool_call_id)
                if tool_call_id not in emitted_tool_call_ids:
                    await flush_text_message(is_final=False)
                    emitted_tool_call_ids.add(tool_call_id)
                    await event_sink("token", codex_tool_token(tool_call))
                    await event_sink("message", tool_call)
            tool_return = codex_tool_return_event(note)
            if tool_return is not None:
                tool_call_id = str(tool_return["tool_call_id"])
                if tool_call_id not in emitted_tool_call_ids:
                    fallback_tool_call = codex_tool_call_event(note)
                    if fallback_tool_call is not None:
                        await flush_text_message(is_final=False)
                        emitted_tool_call_ids.add(tool_call_id)
                        await event_sink("token", codex_tool_token(fallback_tool_call))
                        await event_sink("message", fallback_tool_call)
                await event_sink("message", tool_return)
                active_tool_call_ids.discard(tool_call_id)
            if codex_completed_assistant_message(note):
                await flush_text_message(is_final=True)
        if str(note.get("method") or "") == "turn/completed":
            await flush_text_message(is_final=True)
            error = codex_turn_completed_error(note)
            if error:
                return {
                    "command": command,
                    "cwd": str(cwd),
                    "returncode": 1,
                    "stdout": "".join(output_parts).strip(),
                    "stderr": error,
                    "streamed_tokens": event_sink is not None,
                    "streamed_messages": streamed_messages,
                }
            break
    else:
        raise TimeoutError("codex app-server turn timed out")
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": 0,
        "stdout": "".join(output_parts).strip(),
        "stderr": "",
        "streamed_tokens": event_sink is not None,
        "streamed_messages": streamed_messages,
    }


async def _handle_codex_server_request(client: JsonRpcProcess, request: dict[str, Any]) -> None:
    from ..mcp import looks_like_lemma_mcp_payload
    method = str(request.get("method") or "")
    request_id = request.get("id")
    params = request.get("params")
    if method == "item/permissions/requestApproval" and looks_like_lemma_mcp_payload(params):
        daemon_log("codex auto-accept lemma mcp permission", params)
        await client.respond(request_id, {"decision": "accept"})
        return
    if method == "mcpServer/elicitation/request" and looks_like_lemma_mcp_payload(params):
        daemon_log("codex auto-accept lemma mcp elicitation", params)
        await client.respond(request_id, {"action": "accept", "content": None, "_meta": None})
        return
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
    }:
        daemon_log("codex decline native host permission", {"method": method, "params": params})
        await client.respond(request_id, {"decision": "decline"})
        return
    await client.respond_error(request_id, f"Unsupported method: {method}")


def codex_text_delta(notification: dict[str, Any]) -> str | None:
    method = str(notification.get("method") or "")
    if method not in {"item/agentMessage/delta", "item/outputText/delta"}:
        return None
    params = notification.get("params")
    if not isinstance(params, dict):
        return None
    for key in ("delta", "text", "content"):
        value = params.get(key)
        if isinstance(value, str):
            return value
    return None


def codex_completed_assistant_text(notification: dict[str, Any]) -> str | None:
    item = codex_completed_assistant_item(notification)
    if not item:
        return None
    return _extract_text_from_codex_item(item)


def codex_completed_assistant_message(notification: dict[str, Any]) -> bool:
    return bool(codex_completed_assistant_item(notification))


def codex_completed_assistant_item(notification: dict[str, Any]) -> dict[str, Any]:
    method = str(notification.get("method") or "")
    if method != "item/completed":
        return {}
    item = _codex_notification_item(notification)
    if not item:
        return {}
    if codex_tool_details(item) is not None:
        return {}
    role = str(item.get("role") or "").lower()
    item_type = str(item.get("type") or "").lower()
    if role and role != "assistant":
        return {}
    if role != "assistant" and not any(marker in item_type for marker in ("message", "outputtext", "agent")):
        return {}
    return item


def codex_new_completed_assistant_text(
    output_parts: list[str],
    text_buffer: list[str],
    completed_text: str | None,
) -> str | None:
    if not completed_text:
        return None
    existing = "".join(output_parts)
    current = "".join(text_buffer)
    if existing.strip().endswith(completed_text.strip()):
        return None
    if completed_text.startswith(existing):
        return completed_text[len(existing):] or None
    if current and completed_text.startswith(current):
        return completed_text[len(current):] or None
    return completed_text


def codex_tool_call_event(notification: dict[str, Any]) -> dict[str, object] | None:
    method = str(notification.get("method") or "")
    if method not in {"item/started", "item/completed"}:
        return None
    item = _codex_notification_item(notification)
    tool = codex_tool_details(item)
    if tool is None:
        return None
    tool_name, tool_call_id, tool_input = tool
    return {
        "role": "assistant",
        "kind": "tool_call",
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_args": tool_input,
        "metadata": {
            "tool_name": tool_name,
            "provider": "CODEX",
            "codex_item_type": str(item.get("type") or ""),
        },
    }


def codex_tool_return_event(notification: dict[str, Any]) -> dict[str, object] | None:
    method = str(notification.get("method") or "")
    if method != "item/completed":
        return None
    item = _codex_notification_item(notification)
    tool = codex_tool_details(item)
    if tool is None:
        return None
    tool_name, tool_call_id, _tool_input = tool
    return {
        "role": "tool",
        "kind": "tool_return",
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_result": _codex_tool_output(item),
        "metadata": {
            "tool_name": tool_name,
            "provider": "CODEX",
            "codex_item_type": str(item.get("type") or ""),
        },
    }


def codex_tool_token(tool_call_event: dict[str, object]) -> dict[str, str]:
    return {
        "kind": "tool",
        "data": json.dumps(
            {
                "tool_name": tool_call_event["tool_name"],
                "args": tool_call_event["tool_args"],
            },
            separators=(",", ":"),
        ),
    }


def codex_turn_completed_error(notification: dict[str, Any]) -> str | None:
    params = notification.get("params")
    if not isinstance(params, dict):
        return None
    turn = params.get("turn")
    turn = turn if isinstance(turn, dict) else {}
    status = str(turn.get("status") or params.get("status") or "").lower()
    error = turn.get("error") if "error" in turn else params.get("error")
    if status not in {"failed", "error"} and not error:
        return None
    if isinstance(error, dict):
        return str(error.get("message") or json.dumps(error, sort_keys=True))
    return str(error or f"Codex turn completed with status {status or 'failed'}")


def codex_tool_details(item: dict[str, Any]) -> tuple[str, str, object] | None:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        command = item.get("command")
        if not isinstance(command, str) or not command:
            return None
        tool_call_id = item.get("id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            tool_call_id = f"codex-{uuid4().hex}"
        tool_input: dict[str, object] = {"command": command}
        cwd = item.get("cwd")
        if isinstance(cwd, str) and cwd:
            tool_input["cwd"] = cwd
        source = item.get("source")
        if isinstance(source, str) and source:
            tool_input["source"] = source
        return "commandExecution", tool_call_id, tool_input
    tool_value = item.get("tool") or item.get("name")
    if not tool_value and "tool" in item_type.lower():
        tool_value = item_type
    if not isinstance(tool_value, str) or not tool_value:
        return None
    tool_call_id = item.get("id") or item.get("callId") or item.get("toolCallId")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        tool_call_id = f"codex-{uuid4().hex}"
    return tool_value, tool_call_id, parse_jsonish(item.get("arguments", {}))


def _codex_notification_item(notification: dict[str, Any]) -> dict[str, Any]:
    params = notification.get("params")
    if not isinstance(params, dict):
        return {}
    item = params.get("item")
    return item if isinstance(item, dict) else {}


def _codex_notification_item_id(notification: dict[str, Any]) -> str | None:
    params = notification.get("params")
    if not isinstance(params, dict):
        return None
    item = params.get("item")
    if isinstance(item, dict):
        for key in ("id", "callId", "toolCallId", "itemId", "item_id"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("itemId", "item_id", "id", "callId", "toolCallId"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _codex_delta_belongs_to_active_tool(
    notification: dict[str, Any],
    *,
    active_tool_call_ids: set[str],
    delta: str,
) -> bool:
    if not active_tool_call_ids:
        return False
    item_id = _codex_notification_item_id(notification)
    if item_id is not None:
        return item_id in active_tool_call_ids
    item = _codex_notification_item(notification)
    if item:
        tool = codex_tool_details(item)
        if tool is not None:
            return tool[1] in active_tool_call_ids
    return len(active_tool_call_ids) == 1 and _looks_like_structured_tool_output(delta)


def _looks_like_structured_tool_output(value: str) -> bool:
    stripped = value.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _extract_text_from_codex_item(item: dict[str, Any]) -> str | None:
    if not item:
        return None
    for key in ("text", "content", "output"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    content = item.get("content")
    if isinstance(content, list):
        text = "".join(
            str(part.get("text") or part.get("content") or "")
            for part in content
            if isinstance(part, dict)
            and str(part.get("type") or "text").lower() in {"text", "output_text"}
        )
        if text.strip():
            return text
    for key in ("message", "item"):
        nested = item.get(key)
        if isinstance(nested, dict):
            text = _extract_text_from_codex_item(nested)
            if text:
                return text
    return None


def _codex_tool_output(item: dict[str, Any]) -> object:
    if item.get("type") == "commandExecution":
        return {
            "status": item.get("status"),
            "exit_code": item.get("exitCode"),
            "output": item.get("aggregatedOutput"),
        }
    if "error" in item and item.get("error") is not None:
        return {"error": parse_jsonish(item.get("error"))}
    result = item.get("result")
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        content = result.get("content")
        if isinstance(content, list):
            text_parts = [
                str(part.get("text"))
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if text_parts:
                return "\n".join(text_parts)
        if "text" in result:
            return result.get("text")
        if "output" in result:
            return result.get("output")
    return parse_jsonish(result)


def _codex_saved_session_error_is_recoverable(exc: JsonRpcRequestError) -> bool:
    detail = str(exc).lower()
    return (
        exc.method == "turn/start"
        and "thread" in detail
        and any(marker in detail for marker in ("not found", "missing", "expired"))
    )


def _drain_queue(queue: asyncio.Queue[Any]) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


def _daemon_session_started_payload(*, harness_kind: str, session_id: str) -> dict[str, Any]:
    return {
        "status": "daemon.session.started",
        "local_session": {"harness_kind": harness_kind, "session_id": session_id},
    }


def _daemon_session_invalid_payload(*, harness_kind: str, session_id: str) -> dict[str, Any]:
    return {
        "status": "daemon.session.invalid",
        "local_session": {"harness_kind": harness_kind, "session_id": session_id},
    }


def _provider_prompt_text(*, system_prompt: str, user_prompt: str) -> str:
    if not system_prompt:
        return user_prompt
    return "\n\n".join(
        part
        for part in (system_prompt, _conversation_section(user_prompt))
        if part.strip()
    )


def _conversation_section(user_prompt: str) -> str:
    if not user_prompt:
        return ""
    if user_prompt.lstrip().startswith("#"):
        return user_prompt
    return "# Conversation\n" + user_prompt
