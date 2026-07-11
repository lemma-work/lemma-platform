from __future__ import annotations

import asyncio
import contextlib
import shlex
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from agentbox_client import AgentBoxClient
from app.core.log.log import get_logger
from app.modules.workspace.contracts import (
    PythonExecutionResult,
    ShellCommandResult,
)
from app.modules.workspace.agentbox_retry import (
    DEFAULT_INITIAL_RETRY_DELAY_SECONDS as _SESSION_CREATE_INITIAL_RETRY_DELAY_SECONDS,
    DEFAULT_MAX_ATTEMPTS as _SESSION_CREATE_MAX_ATTEMPTS,
    DEFAULT_MAX_RETRY_DELAY_SECONDS as _SESSION_CREATE_MAX_RETRY_DELAY_SECONDS,
    RETRYABLE_TRANSPORT_ERRORS as _RETRYABLE_TRANSPORT_ERRORS,
    format_http_status_error as _format_http_status_error,
    format_transport_error as _format_transport_error,
    is_retryable_http_error as _is_retryable_http_error,
)

logger = get_logger(__name__)


def _agentbox_command_failure(
    *,
    error: str,
    retryable: bool,
    process_id: str | None = None,
    exit_code: int | None = None,
    completed: bool | None = None,
) -> dict[str, Any]:
    retry_hint = (
        " This looks transient; retry the tool call or recreate the session before giving up."
        if retryable
        else ""
    )
    return {
        "success": False,
        "stdout": "",
        "stderr": "",
        "exit_code": None if retryable else exit_code,
        "completed": (not retryable) if completed is None else completed,
        "process_id": process_id,
        "error": f"{error}{retry_hint}",
    }


class AgentBoxWorkspaceSession:
    """Workspace session adapter that executes directly through AgentBox."""

    def __init__(
        self,
        *,
        client: AgentBoxClient,
        sandbox_id: str,
        session_id: str | None = None,
        env_vars: dict[str, str] | None = None,
        initial_cwd: str = "/workspace",
        auto_close: bool = True,
        activity_callback=None,
        heartbeat_interval_seconds: float = 30.0,
        owns_client: bool = True,
    ) -> None:
        self.client = client
        self.sandbox_id = sandbox_id
        self.session_id = session_id or f"session-{uuid4().hex}"
        self.env_vars = env_vars or {}
        self._cwd = initial_cwd
        self.auto_close = auto_close
        # When the client is pooled/shared across tool calls, this session must
        # not tear down its httpx connection pool on exit (that would force a new
        # TLS handshake to the manager on every call).
        self._owns_client = owns_client
        self._activity_callback = activity_callback
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def _touch_activity(self) -> None:
        if self._activity_callback is not None:
            await self._activity_callback(self.session_id)

    async def execute_code(self, code: str, timeout: int = 60) -> PythonExecutionResult:
        await self._touch_activity()
        try:
            result = await self.client.execute_code(
                self.sandbox_id,
                self.session_id,
                code,
                timeout_seconds=timeout,
            )
        except httpx.HTTPStatusError as exc:
            error = _format_http_status_error(exc)
            retryable = _is_retryable_http_error(exc)
            logger.warning(
                "AgentBox execute_code request failed for sandbox=%s session=%s retryable=%s: %s",
                self.sandbox_id,
                self.session_id,
                retryable,
                error,
            )
            return PythonExecutionResult(
                success=False,
                stdout="",
                stderr="",
                result=None,
                error_in_exec={
                    "ename": "TransientAgentBoxError"
                    if retryable
                    else "AgentBoxHTTPError",
                    "evalue": error,
                    "traceback": [],
                },
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.WriteError,
            httpx.WriteTimeout,
            OSError,
        ) as exc:
            error = _format_transport_error(exc)
            logger.warning(
                "AgentBox execute_code transport failure for sandbox=%s session=%s: %s",
                self.sandbox_id,
                self.session_id,
                error,
            )
            return PythonExecutionResult(
                success=False,
                stdout="",
                stderr="",
                result=None,
                error_in_exec={
                    "ename": "TransientAgentBoxTransportError",
                    "evalue": (
                        "AgentBox transport failed before a response was received. "
                        f"{error}"
                    ),
                    "traceback": [],
                },
            )
        success = int(result.exit_code or 0) == 0
        error = None
        if not success:
            error = {
                "ename": result.error_name or "ExecutionError",
                "evalue": result.stderr or "",
                "traceback": (result.stderr or "").splitlines(),
            }
        return PythonExecutionResult(
            success=success,
            stdout=result.stdout,
            stderr=result.stderr,
            result=result.result,
            error_in_exec=error,
        )

    async def execute_terminal_command(
        self,
        command: str,
        timeout: int = 300,
    ) -> ShellCommandResult:
        result = await self.exec_command(cmd=command, timeout=timeout)
        exit_code = result.get("exit_code")
        success = exit_code == 0
        return ShellCommandResult(
            success=success,
            exit_code=exit_code,
            stdout=result.get("stdout", ""),
            stderr=result.get("stderr", ""),
            error=result.get("error"),
            current_working_directory=self._cwd,
        )

    async def exec_command(
        self,
        *,
        cmd: str,
        max_output_tokens: int | None = None,
        tty: bool = False,
        workdir: str | None = None,
        yield_time_ms: int | None = None,
        timeout: int | None = 300,
    ) -> dict[str, Any]:
        await self._touch_activity()
        try:
            response = await self.client.exec_command(
                self.sandbox_id,
                session_id=self.session_id,
                cmd=cmd,
                max_output_tokens=max_output_tokens,
                tty=tty,
                workdir=workdir or self._cwd,
                yield_time_ms=yield_time_ms,
                timeout=timeout,
            )
            return response.model_dump()
        except httpx.HTTPStatusError as exc:
            error = _format_http_status_error(exc)
            retryable = _is_retryable_http_error(exc)
            logger.warning(
                "AgentBox exec_command request failed for sandbox=%s session=%s retryable=%s: %s",
                self.sandbox_id,
                self.session_id,
                retryable,
                error,
            )
            return _agentbox_command_failure(
                error=error,
                retryable=retryable,
                exit_code=exc.response.status_code,
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.WriteError,
            httpx.WriteTimeout,
            OSError,
        ) as exc:
            error = _format_transport_error(exc)
            logger.warning(
                "AgentBox exec_command transport failure for sandbox=%s session=%s: %s",
                self.sandbox_id,
                self.session_id,
                error,
            )
            return _agentbox_command_failure(
                error=(
                    "AgentBox transport failed before a response was received. "
                    "The command may still be running in the workspace. "
                    f"{error}"
                ),
                retryable=True,
                completed=True,
            )

    async def write_stdin(
        self,
        *,
        process_id: str,
        chars: str | None = None,
        max_output_tokens: int | None = None,
        yield_time_ms: int | None = None,
    ) -> dict[str, Any]:
        await self._touch_activity()
        try:
            response = await self.client.write_stdin(
                self.sandbox_id,
                self.session_id,
                process_id=process_id,
                chars=chars,
                max_output_tokens=max_output_tokens,
                yield_time_ms=yield_time_ms,
            )
            return response.model_dump()
        except httpx.HTTPStatusError as exc:
            error = _format_http_status_error(exc)
            retryable = _is_retryable_http_error(exc)
            logger.warning(
                "AgentBox write_stdin request failed for sandbox=%s session=%s "
                "process=%s retryable=%s: %s",
                self.sandbox_id,
                self.session_id,
                process_id,
                retryable,
                error,
            )
            return _agentbox_command_failure(
                error=error,
                retryable=retryable,
                process_id=process_id,
                exit_code=exc.response.status_code,
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.WriteError,
            httpx.WriteTimeout,
            OSError,
        ) as exc:
            error = _format_transport_error(exc)
            logger.warning(
                "AgentBox write_stdin transport failure for sandbox=%s session=%s process=%s: %s",
                self.sandbox_id,
                self.session_id,
                process_id,
                error,
            )
            return _agentbox_command_failure(
                error=(
                    "AgentBox stdin transport failed before a response was received. "
                    "The process may still be running in the workspace. "
                    f"{error}"
                ),
                retryable=True,
                process_id=process_id,
            )

    async def terminate_process(self, process_id: str) -> dict[str, Any]:
        await self._touch_activity()
        response = await self.client.terminate_process(
            self.sandbox_id,
            self.session_id,
            process_id,
        )
        return response.model_dump()

    async def list_processes(self) -> list[dict[str, Any]]:
        await self._touch_activity()
        response = await self.client.list_processes(
            self.sandbox_id,
            self.session_id,
        )
        return [process.model_dump() for process in response.processes]

    async def set_cwd(self, path: str) -> None:
        resolved_path = await self._resolve_path(path)
        await self.exec_command(cmd=f"mkdir -p {shlex.quote(resolved_path)}", timeout=30)
        self._cwd = resolved_path

    async def get_cwd(self) -> str:
        result = await self.exec_command(cmd="pwd", timeout=5)
        if result.get("success") and result.get("stdout"):
            self._cwd = str(result["stdout"]).strip()
        return self._cwd

    async def _resolve_path(self, path: str) -> str:
        if path.startswith("/"):
            return path
        return str(Path(self._cwd) / path)

    async def wait_for_ready(self, timeout: int = 180) -> None:
        del timeout
        return None

    async def close(self) -> None:
        try:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None
            if self.auto_close and self.session_id:
                try:
                    await self.client.delete_session(self.sandbox_id, self.session_id)
                except Exception as exc:
                    logger.warning(
                        "Error closing AgentBox session sandbox=%s session=%s: %s",
                        self.sandbox_id,
                        self.session_id,
                        exc,
                    )
        finally:
            if self._owns_client:
                try:
                    await self.client.close()
                except Exception as exc:
                    logger.warning("Error closing AgentBox client: %s", exc)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            with contextlib.suppress(Exception):
                await self.client.heartbeat_session(self.sandbox_id, self.session_id)

    async def __aenter__(self):
        await self._create_session_with_retry()
        if self.heartbeat_interval_seconds > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self._touch_activity()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _create_session_with_retry(self) -> None:
        delay = _SESSION_CREATE_INITIAL_RETRY_DELAY_SECONDS
        for attempt in range(1, _SESSION_CREATE_MAX_ATTEMPTS + 1):
            try:
                await self.client.create_session(
                    self.sandbox_id,
                    self.session_id,
                    env=self.env_vars,
                    cwd=self._cwd,
                )
                return
            except httpx.HTTPStatusError as exc:
                if not _is_retryable_http_error(exc):
                    raise
                if attempt == _SESSION_CREATE_MAX_ATTEMPTS:
                    raise
                error = _format_http_status_error(exc)
            except _RETRYABLE_TRANSPORT_ERRORS as exc:
                if attempt == _SESSION_CREATE_MAX_ATTEMPTS:
                    raise
                error = _format_transport_error(exc)

            logger.info(
                "AgentBox session create not ready yet sandbox=%s session=%s attempt=%s/%s: %s",
                self.sandbox_id,
                self.session_id,
                attempt,
                _SESSION_CREATE_MAX_ATTEMPTS,
                error,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, _SESSION_CREATE_MAX_RETRY_DELAY_SECONDS)
