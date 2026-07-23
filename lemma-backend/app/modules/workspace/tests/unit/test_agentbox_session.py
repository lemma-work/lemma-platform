from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from agentbox_client.models import (
    ProcessOutputChannel,
    ProcessOutputChunk,
    ProcessOutputSnapshot,
    ProcessState,
    PythonExecutionState,
    PythonResult,
)
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


def _session(client: _CanonicalClient) -> AgentBoxWorkspaceSession:
    return AgentBoxWorkspaceSession(
        client=client,  # type: ignore[arg-type]
        sandbox_id=uuid4(),
        session_id="conversation-1",
        env_vars={"LEMMA_TOKEN": "dynamic", "LEMMA_BASE_URL": "https://api"},
    )


@pytest.mark.asyncio
async def test_shell_process_uses_typed_environment_and_collects_both_channels() -> None:
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
    assert {item.name: item.value for item in client.python_executes[0]["environment"]} == {
        "LEMMA_BASE_URL": "https://api",
        "LEMMA_TOKEN": "dynamic",
    }
    assert client.deleted_python is True
    assert client.closed is True


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
async def test_transport_failure_returns_operation_identity_without_blind_replay() -> None:
    session = _session(_TransportFailureClient())

    result = await session.exec_command(cmd="non-idempotent-command")

    assert result["success"] is False
    assert result["completed"] is False
    assert UUID(result["process_id"])
    assert "transport failed" in result["error"].lower()
