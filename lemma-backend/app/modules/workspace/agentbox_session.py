from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import time
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx

from agentbox_client import (
    AgentBoxApiError,
    AgentBoxClient,
    EnvironmentVariable,
    FileStat,
    TerminalSize,
    WorkloadKind,
)
from agentbox_client.models import ProcessState, PythonExecutionState
from app.core.log.log import get_logger
from app.modules.workspace.contracts import PythonExecutionResult, ShellCommandResult
from app.modules.workspace.session_support import (
    OutputCursor,
    resize_process_terminal,
    await_python_session_ready,
    agentbox_command_failure as _agentbox_command_failure,
    canonical_runtime_path as _canonical_runtime_path,
    canonical_workspace_cwd,
    with_backpressure as _with_backpressure,
)


logger = get_logger(__name__)
_TERMINAL_PROCESS_STATES = {
    ProcessState.SUCCEEDED,
    ProcessState.FAILED,
    ProcessState.CANCELLED,
    ProcessState.TIMED_OUT,
}


class AgentBoxWorkspaceSession:
    """Backend adapter over AgentBox's process and Python-session protocols."""

    def __init__(
        self,
        *,
        client: AgentBoxClient,
        sandbox_id: str | UUID,
        session_id: str | None = None,
        env_vars: dict[str, str] | None = None,
        initial_cwd: str = "/workspace",
        auto_close: bool = True,
        owns_client: bool = True,
        output_cursor_store=None,
        workspace_recreated: bool = False,
    ) -> None:
        self.client = client
        self.logical_id = UUID(str(sandbox_id))
        self.sandbox_id = str(self.logical_id)
        self.session_id = session_id or str(uuid4())
        self.python_session_id = uuid5(
            NAMESPACE_URL,
            f"agentbox:{self.logical_id}:python:{self.session_id}",
        )
        self.env_vars = env_vars or {}
        self._environment = tuple(
            EnvironmentVariable(name=name, value=value)
            for name, value in sorted(self.env_vars.items())
        )
        self._cwd = canonical_workspace_cwd(initial_cwd)
        self.auto_close = auto_close
        self._owns_client = owns_client
        self._python_session_observed = False
        self._output_cursor = OutputCursor(
            output_cursor_store, sandbox_id=self.sandbox_id
        )
        # True only when this session's durable disk was recreated since it last
        # ran. Callers surface it once so an agent is told its files are gone
        # rather than having to infer it from an empty directory.
        self.workspace_recreated = workspace_recreated

    async def execute_code(self, code: str, timeout: int = 60) -> PythonExecutionResult:
        deadline = self._deadline(timeout)
        try:
            await self._ensure_python_session(deadline)
            operation_id = uuid4()
            result = await _with_backpressure(
                lambda: self.client.execute_python(
                    self.logical_id,
                    self.python_session_id,
                    operation_id=operation_id,
                    code=code,
                    environment=self._environment,
                    output_limit_bytes=1024 * 1024,
                    deadline_at=deadline,
                ),
                deadline,
            )
        except (AgentBoxApiError, httpx.HTTPError, OSError) as exc:
            return PythonExecutionResult(
                success=False,
                stdout="",
                stderr="",
                result=None,
                error_in_exec={
                    "ename": type(exc).__name__,
                    "evalue": str(exc),
                    "traceback": [],
                },
            )
        success = result.state == PythonExecutionState.SUCCEEDED
        error = None
        if not success:
            error = {
                "ename": result.error_name or result.state.value,
                "evalue": result.error_message or result.stderr,
                "traceback": (result.traceback or "").splitlines(),
            }
        return PythonExecutionResult(
            success=success,
            stdout=result.stdout,
            stderr=result.stderr,
            result=result.result,
            error_in_exec=error,
        )

    async def execute_terminal_command(
        self, command: str, timeout: int = 300
    ) -> ShellCommandResult:
        result = await self.exec_command(cmd=command, timeout=timeout)
        return ShellCommandResult(
            # `success` means the command was dispatched and has not failed -
            # the same meaning `exec_command` gives it, so the two callers of
            # one underlying operation cannot disagree. Whether it has finished
            # and with what status is reported by `exit_code`; a command that
            # yielded while still running has no exit code yet and is not a
            # failure.
            success=bool(result.get("success")),
            exit_code=result.get("exit_code"),
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
        cols: int = 120,
        rows: int = 40,
    ) -> dict[str, Any]:
        operation_id = uuid4()
        deadline = self._deadline(timeout or 300)
        output_limit = min(
            2 * 1024 * 1024,
            max(1024, (max_output_tokens or 1_000_000) * 4),
        )
        try:
            await _with_backpressure(
                lambda: self.client.start_process(
                    WorkloadKind.WORKSPACE,
                    self.logical_id,
                    operation_id=operation_id,
                    shell_command=cmd,
                    cwd=workdir or self._cwd,
                    environment=self._environment,
                    tty=TerminalSize(cols=cols, rows=rows) if tty else None,
                    output_limit_bytes=output_limit,
                    deadline_at=deadline,
                ),
                deadline,
            )
            effective_yield_ms = yield_time_ms
            if effective_yield_ms is None and tty:
                effective_yield_ms = 1000
            return await self._collect_process(
                operation_id,
                deadline_at=deadline,
                yield_time_ms=effective_yield_ms,
            )
        except AgentBoxApiError as exc:
            return _agentbox_command_failure(
                error=f"AgentBox {exc.code}: {exc}",
                retryable=exc.retry.value != "do_not_retry",
                process_id=str(operation_id),
            )
        except (httpx.HTTPError, OSError) as exc:
            return _agentbox_command_failure(
                error=f"AgentBox transport failed: {type(exc).__name__}: {exc}",
                retryable=True,
                process_id=str(operation_id),
            )

    async def write_stdin(
        self,
        *,
        process_id: str,
        chars: str | None = None,
        max_output_tokens: int | None = None,
        yield_time_ms: int | None = None,
    ) -> dict[str, Any]:
        del max_output_tokens
        operation_id = UUID(process_id)
        deadline = self._deadline(35)
        input_accepted = False
        try:
            if chars:
                await self.client.send_process_input(
                    WorkloadKind.WORKSPACE,
                    self.logical_id,
                    operation_id,
                    chars.encode(),
                    deadline_at=deadline,
                )
                input_accepted = True
            return await self._collect_process(
                operation_id,
                deadline_at=deadline,
                yield_time_ms=yield_time_ms if yield_time_ms is not None else 5000,
            )
        except AgentBoxApiError as exc:
            if input_accepted:
                return _agentbox_command_failure(
                    error=(
                        "AgentBox accepted the input, but subsequent process "
                        f"status collection failed ({exc.code}: {exc}). Poll "
                        "again without resending the input."
                    ),
                    retryable=False,
                    process_id=process_id,
                    completed=False,
                )
            return _agentbox_command_failure(
                error=f"AgentBox {exc.code}: {exc}",
                retryable=exc.retry.value != "do_not_retry",
                process_id=process_id,
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if not chars:
                return _agentbox_command_failure(
                    error=(
                        "AgentBox process status polling failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    retryable=True,
                    process_id=process_id,
                    completed=False,
                )
            # stdin is not idempotent. A lost response may mean the bytes were
            # accepted, so transport ambiguity must never encourage replay.
            return _agentbox_command_failure(
                error=(
                    (
                        "AgentBox accepted the input, but subsequent process "
                        "status collection failed; poll again without resending "
                        "the input: "
                    )
                    if input_accepted
                    else "AgentBox process input outcome is unknown: "
                )
                + (
                    f"{type(exc).__name__}: {exc}"
                ),
                retryable=False,
                process_id=process_id,
                completed=False,
            )

    async def resize_terminal(
        self, *, process_id: str, cols: int, rows: int
    ) -> dict[str, Any]:
        """Resize an interactive process's terminal.

        Programs that render to a TTY lay out against the terminal size they
        were given, so a fixed size makes wide tables and full-screen UIs wrap
        into unreadable output.
        """

        return await resize_process_terminal(
            self.client,
            self.logical_id,
            process_id,
            cols=cols,
            rows=rows,
            deadline_at=self._deadline(30),
        )

    async def terminate_process(self, process_id: str) -> dict[str, Any]:
        operation_id = UUID(process_id)
        response = await self.client.terminate_process(
            WorkloadKind.WORKSPACE,
            self.logical_id,
            operation_id,
            deadline_at=self._deadline(35),
        )
        return {
            "success": True,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "completed": True,
            "process_id": str(response.operation_id),
            "error": None,
        }

    async def list_processes(self) -> list[dict[str, Any]]:
        processes = await self.client.list_processes(
            WorkloadKind.WORKSPACE, self.logical_id
        )
        return [
            {
                "process_id": str(process.operation_id),
                "cmd": "",
                "cwd": process.cwd,
                "tty": process.tty,
                "started_at": (
                    process.started_at.timestamp() if process.started_at else 0
                ),
                "completed": process.state in _TERMINAL_PROCESS_STATES,
                "exit_code": process.exit_code,
            }
            for process in processes
        ]

    async def stat_file(self, path: str, *, timeout: int = 30) -> FileStat:
        return await self.client.stat_file(
            self.logical_id,
            await self._resolve_path(path),
            deadline_at=self._deadline(timeout),
        )

    async def list_files(self, path: str, *, timeout: int = 30) -> tuple[FileStat, ...]:
        return await self.client.list_files(
            self.logical_id,
            await self._resolve_path(path),
            deadline_at=self._deadline(timeout),
        )

    async def read_file(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
        timeout: int = 60,
    ) -> bytes:
        return await self.client.read_file(
            self.logical_id,
            await self._resolve_path(path),
            offset=offset,
            length=length,
            deadline_at=self._deadline(timeout),
        )

    @asynccontextmanager
    async def stream_file(
        self,
        path: str,
        *,
        offset: int = 0,
        length: int | None = None,
        timeout: int = 60,
    ) -> AsyncIterator[AsyncIterator[bytes]]:
        async with self.client.stream_file(
            self.logical_id,
            await self._resolve_path(path),
            offset=offset,
            length=length,
            deadline_at=self._deadline(timeout),
        ) as stream:
            yield stream

    async def write_file(
        self,
        path: str,
        data: bytes,
        *,
        expected_sha256: str | None = None,
        timeout: int = 60,
    ) -> FileStat:
        return await self.client.write_file(
            self.logical_id,
            await self._resolve_path(path),
            data,
            expected_sha256=expected_sha256,
            deadline_at=self._deadline(timeout),
        )

    async def write_file_stream(
        self,
        path: str,
        data: AsyncIterable[bytes],
        *,
        expected_sha256: str | None = None,
        timeout: int = 60,
    ) -> FileStat:
        return await self.client.write_file_stream(
            self.logical_id,
            await self._resolve_path(path),
            data,
            expected_sha256=expected_sha256,
            deadline_at=self._deadline(timeout),
        )

    async def move_file(
        self,
        source: str,
        destination: str,
        *,
        timeout: int = 30,
    ) -> None:
        await self.client.move_file(
            self.logical_id,
            await self._resolve_path(source),
            await self._resolve_path(destination),
            deadline_at=self._deadline(timeout),
        )

    async def delete_file(
        self,
        path: str,
        *,
        recursive: bool = False,
        timeout: int = 30,
    ) -> None:
        await self.client.delete_file(
            self.logical_id,
            await self._resolve_path(path),
            recursive=recursive,
            deadline_at=self._deadline(timeout),
        )

    # There is deliberately no set_cwd/get_cwd here. Every command runs as a
    # fresh process started at `self._cwd`, and this object is rebuilt on each
    # tool call from the conversation's resolved cwd, so there is no shell
    # whose directory could be moved or queried. `pwd` could only ever echo
    # `self._cwd` back. A `cd` inside one command likewise does not carry to
    # the next; the tool descriptions say so.

    async def _resolve_path(self, path: str) -> str:
        return _canonical_runtime_path(path, base=self._cwd)

    async def wait_for_ready(self, timeout: int = 180) -> None:
        del timeout

    async def close(self) -> None:
        try:
            if self.auto_close and self._python_session_observed:
                try:
                    await self.client.delete_python_session(
                        self.logical_id,
                        self.python_session_id,
                        deadline_at=self._deadline(30),
                    )
                except Exception:
                    logger.debug(
                        "workspace.agentbox_session.python_session_delete_failed",
                        sandbox_id=self.sandbox_id,
                        session_id=self.session_id,
                    )
        finally:
            if self._owns_client:
                await self.client.close()

    async def __aenter__(self) -> AgentBoxWorkspaceSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def _ensure_python_session(self, deadline: datetime) -> None:
        async def create() -> None:
            await self.client.create_python_session(
                self.logical_id,
                self.python_session_id,
                cwd=self._cwd,
                environment_keys=tuple(item.name for item in self._environment),
                deadline_at=deadline,
            )
            self._python_session_observed = True

        await await_python_session_ready(create, deadline)

    async def _collect_process(
        self,
        operation_id: UUID,
        *,
        deadline_at: datetime,
        yield_time_ms: int | None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        yield_seconds = None if yield_time_ms is None else yield_time_ms / 1000
        after_sequence = await self._output_cursor.load(operation_id)
        initial_sequence = after_sequence
        stdout = bytearray()
        stderr = bytearray()
        state = ProcessState.RUNNING
        exit_code: int | None = None
        while datetime.now(timezone.utc) < deadline_at:
            elapsed = time.monotonic() - started
            if yield_seconds is not None and elapsed >= yield_seconds:
                break
            remaining_yield = (
                1.0
                if yield_seconds is None
                else max(0, min(1.0, yield_seconds - elapsed))
            )
            snapshot = await self.client.read_process_output(
                WorkloadKind.WORKSPACE,
                self.logical_id,
                operation_id,
                deadline_at=deadline_at,
                after_sequence=after_sequence,
                wait_seconds=remaining_yield,
            )
            state = snapshot.state
            exit_code = snapshot.exit_code
            for chunk in snapshot.chunks:
                after_sequence = max(after_sequence, chunk.sequence)
                if chunk.channel.value == "stderr":
                    stderr.extend(chunk.data)
                else:
                    stdout.extend(chunk.data)
            self._output_cursor.remember_locally(operation_id, after_sequence)
            if state in _TERMINAL_PROCESS_STATES:
                break
        completed = state in _TERMINAL_PROCESS_STATES
        if after_sequence != initial_sequence:
            await self._output_cursor.save(operation_id, after_sequence)
        # Each poll's bytes are decoded as a unit, so a chunk boundary inside
        # one poll is handled correctly. A multi-byte character split across
        # two polls still yields one replacement character; holding the partial
        # sequence back is not worth it, because the output cursor is a single
        # sequence over interleaved stdout/stderr chunks and rewinding it could
        # duplicate or drop real output.
        return {
            "success": state in {ProcessState.RUNNING, ProcessState.SUCCEEDED},
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "exit_code": exit_code,
            "completed": completed,
            "process_id": None if completed else str(operation_id),
            "error": None
            if state in {ProcessState.RUNNING, ProcessState.SUCCEEDED}
            else state.value,
        }




    @staticmethod
    def _deadline(seconds: int | float) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
