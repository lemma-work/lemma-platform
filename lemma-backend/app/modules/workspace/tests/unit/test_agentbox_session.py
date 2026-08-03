from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from agentbox_client.models import (
    AgentBoxErrorBody,
    AgentBoxErrorResponse,
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessState,
    PythonExecutionState,
    PythonResult,
    RetryDisposition,
)
from agentbox_client import AgentBoxApiError
from app.modules.workspace.agentbox_session import AgentBoxWorkspaceSession


class _CanonicalClient:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.inputs: list[bytes] = []
        self.python_creates: list[dict[str, Any]] = []
        self.python_executes: list[dict[str, Any]] = []
        self.deleted_python = False
        self.closed = False
        self._reads = 0

    async def start_process(self, *_args: Any, **kwargs: Any) -> None:
        self.started.append(kwargs)

    async def read_process_output(
        self, *_args: Any, **_kwargs: Any
    ) -> ProcessOutputSnapshot:
        self._reads += 1
        if self._reads == 1:
            return ProcessOutputSnapshot(
                chunks=(
                    ProcessOutputChunk(
                        sequence=1,
                        channel=ProcessOutputChannel.STDOUT,
                        data=b"hello\n",
                    ),
                ),
                next_sequence=2,
                truncated_before_sequence=None,
                state=ProcessState.RUNNING,
                exit_code=None,
            )
        return ProcessOutputSnapshot(
            chunks=(
                ProcessOutputChunk(
                    sequence=2,
                    channel=ProcessOutputChannel.STDERR,
                    data=b"warning\n",
                ),
            ),
            next_sequence=3,
            truncated_before_sequence=None,
            state=ProcessState.SUCCEEDED,
            exit_code=0,
        )

    async def send_process_input(
        self, *_args: Any, data: bytes | None = None, **_kwargs: Any
    ) -> None:
        # AgentBoxClient accepts data positionally; preserve either fake form.
        if data is not None:
            self.inputs.append(data)

    async def create_python_session(self, *_args: Any, **kwargs: Any) -> None:
        self.python_creates.append(kwargs)

    async def execute_python(self, *_args: Any, **kwargs: Any) -> PythonResult:
        self.python_executes.append(kwargs)
        return PythonResult(
            operation_id=kwargs["operation_id"],
            state=PythonExecutionState.SUCCEEDED,
            stdout="native\n",
            stderr="",
            result="42",
            error_name=None,
            error_message=None,
            traceback=None,
            output_truncated=False,
        )

    async def delete_python_session(self, *_args: Any, **_kwargs: Any) -> None:
        self.deleted_python = True

    async def close(self) -> None:
        self.closed = True


class _TransportFailureClient(_CanonicalClient):
    async def start_process(self, *_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "https://agentbox.test/processes")
        raise httpx.ReadTimeout("lost response", request=request)


class _PythonAllocationFailureClient(_CanonicalClient):
    def __init__(self) -> None:
        super().__init__()
        self.python_create_calls = 0

    async def create_python_session(self, *_args: Any, **_kwargs: Any) -> None:
        self.python_create_calls += 1
        response = httpx.Response(
            409,
            request=httpx.Request(
                "PUT", "https://agentbox.test/python-sessions/session"
            ),
        )
        raise AgentBoxApiError(
            response,
            AgentBoxErrorResponse(
                error=AgentBoxErrorBody(
                    code="ALLOCATION_CHANGED",
                    message="retry after allocation replacement",
                    retry=RetryDisposition.DO_NOT_RETRY,
                )
            ),
        )


def _session(client: _CanonicalClient) -> AgentBoxWorkspaceSession:
    return AgentBoxWorkspaceSession(
        client=client,  # type: ignore[arg-type]
        sandbox_id=uuid4(),
        session_id="conversation-1",
        env_vars={"LEMMA_TOKEN": "dynamic", "LEMMA_BASE_URL": "https://api"},
    )


class _CapacityThenSucceedClient:
    """Rejects the first start with a retryable capacity signal, then accepts."""

    def __init__(self, rejections: int = 2) -> None:
        self.remaining_rejections = rejections
        self.start_operation_ids: list[Any] = []

    async def start_process(self, *_args: Any, **kwargs: Any) -> None:
        self.start_operation_ids.append(kwargs["operation_id"])
        if self.remaining_rejections > 0:
            self.remaining_rejections -= 1
            response = httpx.Response(
                429,
                request=httpx.Request("POST", "https://agentbox.test/processes"),
            )
            raise AgentBoxApiError(
                response,
                AgentBoxErrorResponse(
                    error=AgentBoxErrorBody(
                        code="CAPACITY_EXHAUSTED",
                        message="manager process routing capacity is full",
                        retry=RetryDisposition.WAIT,
                        retry_after_ms=1,
                    )
                ),
            )

    async def read_process_output(
        self, *_args: Any, **_kwargs: Any
    ) -> ProcessOutputSnapshot:
        return ProcessOutputSnapshot(
            chunks=(
                ProcessOutputChunk(
                    sequence=1,
                    channel=ProcessOutputChannel.STDOUT,
                    data=b"ok\n",
                ),
            ),
            next_sequence=2,
            truncated_before_sequence=None,
            state=ProcessState.SUCCEEDED,
            exit_code=0,
        )


@pytest.mark.asyncio
async def test_capacity_pressure_is_absorbed_instead_of_failing_the_tool_call() -> None:
    """A WAIT signal must not become an agent-visible tool failure.

    Capacity is rejected before anything is dispatched, so asking the agent to
    retry made it the retry loop for a platform-level limit and amplified the
    load that caused it. Retries reuse the operation ID, so the manager still
    deduplicates.
    """

    client = _CapacityThenSucceedClient(rejections=2)
    session = AgentBoxWorkspaceSession(
        client=client,  # type: ignore[arg-type]
        sandbox_id=uuid4(),
        session_id="conversation-1",
    )

    result = await session.exec_command(cmd="echo ok", timeout=30)

    assert result["success"] is True
    assert result["stdout"] == "ok\n"
    assert result["error"] is None
    assert len(client.start_operation_ids) == 3
    # One operation identity across all attempts keeps manager dedup effective.
    assert len(set(client.start_operation_ids)) == 1


class _ReplayTrackingClient:
    """A client whose output buffer honours ``after_sequence``, like the manager."""

    def __init__(self) -> None:
        self.requested_after: list[int] = []
        self._buffer = [
            ProcessOutputChunk(
                sequence=index,
                channel=ProcessOutputChannel.STDOUT,
                data=f"line-{index}\n".encode(),
            )
            for index in range(1, 4)
        ]

    async def start_process(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def send_process_input(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def read_process_output(
        self, *_args: Any, **kwargs: Any
    ) -> ProcessOutputSnapshot:
        after = int(kwargs.get("after_sequence") or 0)
        self.requested_after.append(after)
        return ProcessOutputSnapshot(
            chunks=tuple(item for item in self._buffer if item.sequence > after),
            next_sequence=len(self._buffer) + 1,
            truncated_before_sequence=None,
            state=ProcessState.RUNNING,
            exit_code=None,
        )


class _MemoryCursorStore:
    def __init__(self) -> None:
        self.cursors: dict[str, int] = {}

    async def get_output_cursor(self, process_id: str) -> int:
        return self.cursors.get(process_id, 0)

    async def set_output_cursor(
        self, *, process_id: str, sequence: int, ttl_seconds: int = 0
    ) -> None:
        del ttl_seconds
        self.cursors[process_id] = sequence


@pytest.mark.asyncio
async def test_polling_a_process_again_does_not_replay_delivered_output() -> None:
    """A second tool call must not re-read output the first already returned.

    The session object is rebuilt per tool call, so an in-memory cursor
    restarts at zero and every poll of a long-running or interactive process
    re-delivers its whole retained buffer. The shared cursor store is what
    makes the second poll resume where the first stopped.
    """

    client = _ReplayTrackingClient()
    store = _MemoryCursorStore()
    sandbox_id = uuid4()

    def build() -> AgentBoxWorkspaceSession:
        return AgentBoxWorkspaceSession(
            client=client,  # type: ignore[arg-type]
            sandbox_id=sandbox_id,
            session_id="conversation-1",
            output_cursor_store=store,
        )

    first = await build().exec_command(cmd="tail -f log", yield_time_ms=1)
    process_id = first["process_id"]
    assert process_id is not None
    assert first["stdout"] == "line-1\nline-2\nline-3\n"
    # The first poll of a new process starts from the beginning, and the
    # delivered position is remembered outside this session object.
    assert client.requested_after[0] == 0
    assert store.cursors[process_id] == 3

    # A separate tool call, and therefore a brand new session object.
    client.requested_after.clear()
    second = await build().write_stdin(process_id=process_id, yield_time_ms=1)

    assert client.requested_after[0] == 3
    assert second["stdout"] == ""


@pytest.mark.asyncio
async def test_shell_process_uses_typed_environment_and_collects_both_channels() -> (
    None
):
    client = _CanonicalClient()
    session = _session(client)

    result = await session.exec_command(cmd="printf hello")

    assert result["success"] is True
    assert result["completed"] is True
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == "warning\n"
    assert result["exit_code"] == 0
    assert client.started[0]["shell_command"] == "printf hello"
    assert {item.name for item in client.started[0]["environment"]} == {
        "LEMMA_BASE_URL",
        "LEMMA_TOKEN",
    }


@pytest.mark.asyncio
async def test_yielded_process_returns_stable_operation_id_for_reconnect() -> None:
    client = _CanonicalClient()
    session = _session(client)

    result = await session.exec_command(cmd="sleep 30", yield_time_ms=0)

    assert result["success"] is True
    assert result["completed"] is False
    assert UUID(result["process_id"])
    assert client.started[0]["operation_id"] == UUID(result["process_id"])


@pytest.mark.asyncio
async def test_python_session_declares_keys_but_sends_values_per_execution() -> None:
    client = _CanonicalClient()
    session = _session(client)

    result = await session.execute_code("40 + 2")
    await session.close()

    assert result.success is True
    assert result.stdout == "native\n"
    assert result.result == "42"
    assert client.python_creates[0]["environment_keys"] == (
        "LEMMA_BASE_URL",
        "LEMMA_TOKEN",
    )
    assert {
        item.name: item.value for item in client.python_executes[0]["environment"]
    } == {
        "LEMMA_BASE_URL": "https://api",
        "LEMMA_TOKEN": "dynamic",
    }
    assert client.deleted_python is True
    assert client.closed is True


@pytest.mark.asyncio
async def test_unhealthy_python_allocation_fails_once_without_retry_storm() -> None:
    client = _PythonAllocationFailureClient()
    session = _session(client)

    result = await session.execute_code("40 + 2")

    assert result.success is False
    assert result.error_in_exec is not None
    assert result.error_in_exec["ename"] == "AgentBoxApiError"
    assert client.python_create_calls == 1


@pytest.mark.asyncio
async def test_relative_initial_cwd_is_canonicalized_under_workspace() -> None:
    client = _CanonicalClient()
    session = AgentBoxWorkspaceSession(
        client=client,  # type: ignore[arg-type]
        sandbox_id=uuid4(),
        initial_cwd="tasks/function-1",
    )

    await session.execute_code("40 + 2")

    assert client.python_creates[0]["cwd"] == "/workspace/tasks/function-1"


@pytest.mark.asyncio
async def test_workspace_paths_cannot_escape_runtime_roots() -> None:
    client = _CanonicalClient()
    session = _session(client)

    with pytest.raises(ValueError, match="must remain under"):
        await session._resolve_path("../../etc/passwd")
    with pytest.raises(ValueError, match="must remain under"):
        await session._resolve_path("/etc/passwd")

    assert await session._resolve_path("../tmp/result") == "/tmp/result"
    assert await session._resolve_path("/tmp/result") == "/tmp/result"


@pytest.mark.asyncio
async def test_transport_failure_returns_operation_identity_without_blind_replay() -> (
    None
):
    session = _session(_TransportFailureClient())

    result = await session.exec_command(cmd="non-idempotent-command")

    assert result["success"] is False
    assert result["completed"] is False
    assert UUID(result["process_id"])
    assert "transport failed" in result["error"].lower()
