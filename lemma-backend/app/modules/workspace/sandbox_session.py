from __future__ import annotations

import time
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from opentelemetry import trace

from sandbox_runtime.errors import (
    SandboxError,
    SandboxUnavailable,
)
from sandbox_runtime.protocol import (
    EnvironmentVariable,
    FileStat,
    TerminalSize,
    WorkloadKind,
)
from sandbox_runtime.protocol import PythonExecutionState
from app.core.errors.describe import describe_exception
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.contracts import PythonExecutionResult, ShellCommandResult
from app.modules.workspace.process_output import (
    TERMINAL_PROCESS_STATES,
    collect_process_output,
)
from app.modules.workspace.session_support import (
    OutputCursor,
    resize_process_terminal,
    await_python_session_ready,
    sandbox_command_failure as _sandbox_command_failure,
    canonical_runtime_path as _canonical_runtime_path,
    canonical_workspace_cwd,
    with_backpressure as _with_backpressure,
    sandbox_is_responsive,
)


logger = get_logger(__name__)

# How long a pure output poll waits for new bytes. Bounded by write_stdin's own
# 35s deadline.
_POLL_YIELD_MS = 30_000

# The whole budget for deciding whether a silent sandbox is still alive. Short
# on purpose: this runs after a window that already spent 30 seconds telling the
# caller nothing, and `echo` on a healthy sandbox answers in well under a
# second, so anything approaching this bound is itself the answer.
_LIVENESS_PROBE_SECONDS = 8.0

# Stateful interpreters already created, by (sandbox, python session, epoch).
#
# The session object is rebuilt for every tool call, so its own
# `_python_session_observed` flag was always False and `execute_python` opened
# with a create round trip every single time. Keyed by the *container* epoch
# rather than the storage generation: a kernel is memory, so it dies with the
# container hosting it -- the mirror image of a workspace directory, which lives
# on the volume and survives exactly that.
_python_sessions_observed: dict[tuple[UUID, UUID, int], float] = {}
_PYTHON_SESSION_OBSERVED_SECONDS = 60.0
_PYTHON_SESSION_CACHE_MAX = 512
_tracer = trace.get_tracer("app.modules.workspace.tool_phases")


def forget_python_sessions(logical_id: UUID) -> None:
    """Drop remembered interpreters for a sandbox that is going away."""
    for key in [key for key in _python_sessions_observed if key[0] == logical_id]:
        _python_sessions_observed.pop(key, None)


class SandboxWorkspaceSession:
    """Backend adapter over the sandbox runtime's process and Python-session protocols."""

    def __init__(
        self,
        *,
        client: Any,
        sandbox_id: str | UUID,
        session_id: str | None = None,
        env_vars: dict[str, str] | None = None,
        initial_cwd: str = "/workspace",
        auto_close: bool = True,
        owns_client: bool = True,
        output_cursor_store=None,
        workspace_recreated: bool = False,
        allocation_epoch: int | None = None,
    ) -> None:
        self.client = client
        self.logical_id = UUID(str(sandbox_id))
        self.sandbox_id = str(self.logical_id)
        self.session_id = session_id or str(uuid4())
        self.python_session_id = uuid5(
            NAMESPACE_URL,
            f"workspace:{self.logical_id}:python:{self.session_id}",
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
        self._allocation_epoch = allocation_epoch
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
        except (SandboxError, httpx.HTTPError, OSError) as exc:
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
        # Two clocks, deliberately: `wait_until` is how long THIS call waits for
        # output, `process_deadline` is how long the command may live. They used
        # to be one number, so a 60s wait also stamped a 60s lifetime on a
        # ten-minute build — harmless only because nothing enforced it, which
        # the reaper now does.
        wait_until = self._deadline(timeout or 300)
        process_deadline = self._deadline(
            workspace_settings.process_max_lifetime_seconds
        )
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
                    deadline_at=process_deadline,
                ),
                wait_until,
            )
            effective_yield_ms = yield_time_ms
            if effective_yield_ms is None and tty:
                effective_yield_ms = 1000
            return await self._collect_process(
                operation_id,
                deadline_at=wait_until,
                yield_time_ms=effective_yield_ms,
            )
        except SandboxError as exc:
            return _sandbox_command_failure(
                error=f"{describe_exception(exc)}",
                retryable=isinstance(exc, SandboxUnavailable),
                process_id=str(operation_id),
            )
        except (httpx.HTTPError, OSError) as exc:
            return _sandbox_command_failure(
                error=f"the sandbox runtime transport failed: {describe_exception(exc)}",
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
            # A pure poll (`chars=""`) is how an agent watches a build finish, so
            # it waits much longer than an interactive keystroke does: at 5s a
            # ten-minute build costs ~120 model round-trips just to sit still,
            # and each one burns a request against the run's budget. Writing
            # actual input keeps the short window, because someone typing wants
            # the echo back immediately.
            default_yield_ms = 5000 if chars else _POLL_YIELD_MS
            return await self._collect_process(
                operation_id,
                deadline_at=deadline,
                yield_time_ms=(
                    yield_time_ms if yield_time_ms is not None else default_yield_ms
                ),
            )
        except SandboxError as exc:
            if input_accepted:
                return _sandbox_command_failure(
                    error=(
                        "The sandbox accepted the input, but subsequent process "
                        f"status collection failed ({describe_exception(exc)}). "
                        "Poll again without resending the input."
                    ),
                    retryable=False,
                    process_id=process_id,
                    completed=False,
                )
            return _sandbox_command_failure(
                error=f"the sandbox runtime failed: {describe_exception(exc)}",
                retryable=isinstance(exc, SandboxUnavailable),
                process_id=process_id,
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if not chars:
                return _sandbox_command_failure(
                    error=(
                        "the sandbox runtime process status polling failed: "
                        f"{describe_exception(exc)}"
                    ),
                    retryable=True,
                    process_id=process_id,
                    completed=False,
                )
            # stdin is not idempotent. A lost response may mean the bytes were
            # accepted, so transport ambiguity must never encourage replay.
            return _sandbox_command_failure(
                error=(
                    (
                        "the sandbox runtime accepted the input, but subsequent process "
                        "status collection failed; poll again without resending "
                        "the input: "
                    )
                    if input_accepted
                    else "the sandbox runtime process input outcome is unknown: "
                )
                + (f"{describe_exception(exc)}"),
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
                # Absent when the sandbox runtime is the source: it tracks what
                # is running, not what a control plane once recorded about how
                # it was started.
                "cwd": getattr(process, "cwd", None) or "",
                "tty": bool(getattr(process, "tty", None)),
                "started_at": (
                    process.started_at.timestamp() if process.started_at else 0
                ),
                "completed": process.state in TERMINAL_PROCESS_STATES,
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
                        "workspace.sandbox_session.python_session_delete_failed",
                        sandbox_id=self.sandbox_id,
                        session_id=self.session_id,
                    )
        finally:
            if self._owns_client:
                await self.client.close()

    async def __aenter__(self) -> SandboxWorkspaceSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    def _python_session_key(self) -> tuple[UUID, UUID, int] | None:
        if self._allocation_epoch is None:
            return None
        return (self.logical_id, self.python_session_id, self._allocation_epoch)

    async def _ensure_python_session(self, deadline: datetime) -> None:
        key = self._python_session_key()
        if key is not None:
            seen_at = _python_sessions_observed.get(key)
            if (
                seen_at is not None
                and (time.monotonic() - seen_at) < _PYTHON_SESSION_OBSERVED_SECONDS
            ):
                self._python_session_observed = True
                return

        async def create() -> None:
            await self.client.create_python_session(
                self.logical_id,
                self.python_session_id,
                cwd=self._cwd,
                environment_keys=tuple(item.name for item in self._environment),
                deadline_at=deadline,
            )
            self._python_session_observed = True
            if key is not None:
                if len(_python_sessions_observed) >= _PYTHON_SESSION_CACHE_MAX:
                    for stale in sorted(
                        _python_sessions_observed,
                        key=lambda entry: _python_sessions_observed[entry],
                    )[: len(_python_sessions_observed) - _PYTHON_SESSION_CACHE_MAX + 1]:
                        _python_sessions_observed.pop(stale, None)
                _python_sessions_observed[key] = time.monotonic()

        with _tracer.start_as_current_span("lemma.workspace.python_session"):
            await await_python_session_ready(create, deadline)

    async def _collect_process(
        self,
        operation_id: UUID,
        *,
        deadline_at: datetime,
        yield_time_ms: int | None,
    ) -> dict[str, Any]:
        return await collect_process_output(
            self.client,
            self.logical_id,
            self._output_cursor,
            operation_id,
            deadline_at=deadline_at,
            yield_time_ms=yield_time_ms,
            probe_liveness=lambda: sandbox_is_responsive(
                self.client,
                self.logical_id,
                cwd=self._cwd,
                deadline=self._deadline(_LIVENESS_PROBE_SECONDS),
                budget_seconds=_LIVENESS_PROBE_SECONDS,
            ),
        )

    @staticmethod
    def _deadline(seconds: int | float) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
