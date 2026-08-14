"""Supporting concerns for a workspace session: paths, cursors, backpressure.

These are the parts of a session that are not the session: canonicalising a
path, remembering how much of a process's output has already been delivered,
and waiting out a retryable capacity signal. Keeping them here leaves the
session itself about talking to the sandbox runtime.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import posixpath
from typing import Any
from uuid import UUID

import httpx
from redis.exceptions import RedisError

from sandbox_runtime.protocol import TerminalSize, WorkloadKind
from app.core.errors.describe import describe_exception
from app.core.log.log import get_logger
from sandbox_runtime.errors import (
    SandboxError,
    SandboxUnavailable,
)


logger = get_logger(__name__)

RUNTIME_FILESYSTEM_ROOTS = ("/workspace", "/tmp")

# A cursor or a generation marker is a convenience, not a correctness
# requirement, so a store that is down must never fail an agent's tool call.
_STORE_FAILURES = (RedisError, OSError, ValueError, TypeError)


def canonical_runtime_path(value: str, *, base: str = "/workspace") -> str:
    if not value:
        raise ValueError("workspace path must not be empty")
    normalized = posixpath.normpath(
        value if value.startswith("/") else posixpath.join(base, value)
    )
    if any(
        normalized == root or normalized.startswith(f"{root}/")
        for root in RUNTIME_FILESYSTEM_ROOTS
    ):
        return normalized
    raise ValueError("workspace path must remain under /workspace or /tmp")


def canonical_workspace_cwd(value: str) -> str:
    return canonical_runtime_path(value)


def describe_sandbox_failure(exc: BaseException) -> tuple[str, bool]:
    """Normalise either provisioning path's failure into (message, retryable).

    Reduces any sandbox failure to the two facts a tool call needs: what to
    tell the agent, and whether trying again could help. The type carries the
    second one, which is why there is no retry flag to consult.
    """

    from sandbox_runtime.errors import (
        SandboxRejected,
        SandboxUnavailable,
    )

    if isinstance(exc, SandboxUnavailable):
        return (f"Workspace unavailable: {exc}", True)
    if isinstance(exc, SandboxRejected):
        return (f"Workspace refused the operation: {exc}", False)
    return (f"Workspace transport failed: {describe_exception(exc)}", True)


def sandbox_failure_types() -> tuple[type[BaseException], ...]:
    """Every failure a sandbox operation may raise."""

    from sandbox_runtime.errors import SandboxError

    return (SandboxError,)


def sandbox_command_failure(
    *,
    error: str,
    retryable: bool,
    process_id: str | None = None,
    completed: bool | None = None,
) -> dict[str, Any]:
    hint = " Retry the same operation if it is still needed." if retryable else ""
    return {
        "success": False,
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "completed": not retryable if completed is None else completed,
        "process_id": process_id,
        "error": f"{error}{hint}",
    }


async def with_backpressure(operation, deadline: datetime):
    """Wait out a retryable capacity signal instead of failing the caller.

    A WAIT disposition means the manager rejected the call before anything was
    dispatched - the sandbox is still provisioning, or routing capacity is
    momentarily full. Surfacing that as a tool failure asks the agent to be the
    retry loop for a platform-level limit, which amplifies the load that caused
    it. The operation ID is unchanged across attempts, so the manager still
    deduplicates if one did get through.
    """

    attempt = 0
    while True:
        try:
            return await operation()
        except SandboxUnavailable as exc:
            # Retryable by type. `SandboxRejected` and its subclasses are
            # definitive and propagate untouched, which is the same two-way
            # split the manager expressed as a `retry` field on every error.
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise
            delay = max(0.05, (exc.retry_after_ms or 250) / 1000)
            # Back off so a burst of waiting callers does not resynchronise.
            delay = min(delay * (1.5 ** min(attempt, 4)), 5.0, remaining)
            attempt += 1
            await asyncio.sleep(delay)


class OutputCursor:
    """Tracks how much of each process's output has been delivered.

    The session object is rebuilt on every tool call, so an in-process cursor
    only spans a single call. Without a shared one, every poll of a
    long-running or interactive process re-reads its whole retained buffer.
    """

    def __init__(self, store, *, sandbox_id: str) -> None:
        self._store = store
        self._sandbox_id = sandbox_id
        self._local: dict[UUID, int] = {}

    def remember_locally(self, operation_id: UUID, sequence: int) -> None:
        self._local[operation_id] = sequence

    async def load(self, operation_id: UUID) -> int:
        local = self._local.get(operation_id, 0)
        if self._store is None:
            return local
        try:
            stored = await self._store.get_output_cursor(str(operation_id))
        except _STORE_FAILURES:
            logger.debug(
                "workspace.sandbox_session.output_cursor_read_failed",
                sandbox_id=self._sandbox_id,
                process_id=str(operation_id),
            )
            return local
        return max(local, stored)

    async def save(self, operation_id: UUID, sequence: int) -> None:
        self._local[operation_id] = sequence
        if self._store is None:
            return
        try:
            await self._store.set_output_cursor(
                process_id=str(operation_id),
                sequence=sequence,
            )
        except _STORE_FAILURES:
            logger.debug(
                "workspace.sandbox_session.output_cursor_write_failed",
                sandbox_id=self._sandbox_id,
                process_id=str(operation_id),
            )


async def await_python_session_ready(create, deadline: datetime) -> None:
    """Retry creating a stateful interpreter until the sandbox can host one.

    Distinct from `with_backpressure`: creation legitimately fails while an
    allocation is still provisioning, and the caller has nothing useful to do
    but wait. A non-retryable error is raised immediately rather than burning
    the whole deadline on something that will never succeed.
    """

    last_error: SandboxUnavailable | None = None
    while datetime.now(timezone.utc) < deadline:
        try:
            await create()
            return
        except SandboxUnavailable as exc:
            last_error = exc
            remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                break
            delay = max(0.05, (exc.retry_after_ms or 250) / 1000)
            await asyncio.sleep(min(delay, remaining))
    detail = (
        f"; last sandbox error was {last_error.code}: {last_error}"
        if last_error is not None
        else ""
    )
    raise TimeoutError(
        f"Python session did not become ready before its deadline{detail}"
    ) from last_error


async def resize_process_terminal(
    client,
    logical_id: UUID,
    process_id: str,
    *,
    cols: int,
    rows: int,
    deadline_at: datetime,
) -> dict[str, Any]:
    """Resize an interactive process's terminal.

    Programs that render to a TTY lay out against the size they were given, so
    a fixed size makes wide tables and full-screen UIs wrap into unreadable
    output. Failures are returned as a result rather than raised, so a resize
    can never take down the tool call that attempted it.
    """

    try:
        await client.resize_process(
            WorkloadKind.WORKSPACE,
            logical_id,
            UUID(process_id),
            TerminalSize(cols=cols, rows=rows),
            deadline_at=deadline_at,
        )
    except SandboxError as exc:
        return sandbox_command_failure(
            error=f"{describe_exception(exc)}",
            retryable=isinstance(exc, SandboxUnavailable),
            process_id=process_id,
            completed=False,
        )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return sandbox_command_failure(
            error=f"the sandbox runtime terminal resize failed: {describe_exception(exc)}",
            retryable=True,
            process_id=process_id,
            completed=False,
        )
    return {
        "success": True,
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "completed": False,
        "process_id": process_id,
        "error": None,
    }
