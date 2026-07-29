from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ListProcessesRequest,
    TerminateProcessRequest,
    WriteStdinRequest,
)


class _FailingRuntime:
    async def resolve_session_for_process(self, process_id: str) -> str | None:
        del process_id
        return None

    async def get_session(self, **kwargs):
        del kwargs
        raise RuntimeError("agentbox manager returned 500")


class _FakeWorkspaceSession:
    def __init__(self, result: dict, *, session_id: str = "session-1"):
        self.result = result
        self.session_id = session_id
        self.auto_close = False
        self.deleted = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        if self.auto_close:
            self.deleted = True

    async def exec_command(self, **kwargs):
        self.last_exec_kwargs = kwargs
        return self.result

    async def write_stdin(self, **kwargs):
        self.last_stdin_kwargs = kwargs
        return self.result

    async def terminate_process(self, process_id: str):
        self.last_terminate_process_id = process_id
        return self.result

    async def list_processes(self):
        return self.result.get("processes", [])


class _FakeRuntime:
    def __init__(self, result: dict):
        self.result = result
        self.session = _FakeWorkspaceSession(result)
        self.close_on_exit: bool | None = None
        self.session_id: str | None = None
        self.bound_processes: list[tuple[str, str]] = []
        self.process_sessions: dict[str, str] = {}
        self.cleared_processes: list[str] = []

    async def resolve_session_for_process(self, process_id: str) -> str | None:
        return self.process_sessions.get(process_id)

    async def get_session(self, **kwargs):
        self.close_on_exit = kwargs["close_on_exit"]
        self.session_id = kwargs["session_id"]
        self.session.auto_close = bool(kwargs["close_on_exit"])
        return self.session

    async def bind_process_to_session(self, *, process_id: str, session_id: str):
        self.bound_processes.append((process_id, session_id))
        self.process_sessions[process_id] = session_id

    async def clear_process_binding(self, process_id: str):
        self.cleared_processes.append(process_id)
        self.process_sessions.pop(process_id, None)


def _context() -> BaseAgentContext:
    return BaseAgentContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_exec_command_internal_uses_conversation_default_session(
    monkeypatch: pytest.MonkeyPatch,
):
    ctx = _context()
    runtime = _FakeRuntime(
        {
            "success": True,
            "stdout": "/workspace",
            "stderr": "",
            "exit_code": 0,
            "completed": True,
            "process_id": None,
        }
    )
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    result = await workspace_cli.exec_command_internal(
        ctx,
        ExecCommandRequest(cmd="pwd"),
    )

    assert result.success is True
    assert runtime.session_id == f"shell-{ctx.conversation_id.hex}"
    assert runtime.close_on_exit is False
    assert runtime.session.deleted is False
    assert runtime.session.last_exec_kwargs["yield_time_ms"] == 30000


@pytest.mark.asyncio
async def test_exec_command_internal_returns_failure_when_session_setup_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: _FailingRuntime(),
    )

    result = await workspace_cli.exec_command_internal(
        _context(),
        ExecCommandRequest(cmd="pwd"),
    )

    assert result.success is False
    assert result.completed is False
    assert result.exit_code is None
    assert "agentbox manager returned 500" in (result.error or "")
    assert "retry" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_exec_command_internal_keeps_yielded_process_session_open(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeRuntime(
        {
            "success": True,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "completed": False,
            "process_id": "proc-1",
        }
    )
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    result = await workspace_cli.exec_command_internal(
        _context(),
        ExecCommandRequest(cmd="lemma --output json profile", yield_time_ms=1000),
    )

    assert result.success is True
    assert result.completed is False
    assert result.process_id == "proc-1"
    assert runtime.close_on_exit is False
    assert runtime.session.deleted is False
    assert runtime.bound_processes == [("proc-1", "session-1")]


@pytest.mark.asyncio
async def test_exec_command_internal_closes_completed_yielded_session(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeRuntime(
        {
            "success": True,
            "stdout": "done",
            "stderr": "",
            "exit_code": 0,
            "completed": True,
            "process_id": "proc-1",
        }
    )
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    result = await workspace_cli.exec_command_internal(
        _context(),
        ExecCommandRequest(cmd="lemma --output json profile", yield_time_ms=10000),
    )

    assert result.success is True
    assert result.completed is True
    assert result.stdout == "done"
    assert result.process_id is None
    assert runtime.close_on_exit is False
    assert runtime.session.deleted is False
    assert runtime.bound_processes == []


@pytest.mark.asyncio
async def test_write_stdin_internal_routes_by_process_id(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeRuntime(
        {
            "success": True,
            "stdout": "stdin:ok",
            "stderr": "",
            "exit_code": 0,
            "completed": True,
            "process_id": None,
        }
    )
    runtime.process_sessions["proc-1"] = "session-1"
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    result = await workspace_cli.write_stdin_internal(
        _context(),
        WriteStdinRequest(process_id="proc-1", chars="ok\n"),
    )

    assert result.success is True
    assert result.completed is True
    assert result.stdout == "stdin:ok"
    assert result.process_id is None
    assert runtime.session.last_stdin_kwargs["process_id"] == "proc-1"
    assert runtime.cleared_processes == ["proc-1"]


@pytest.mark.asyncio
async def test_write_stdin_internal_falls_back_to_default_session_when_mapping_expired(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeRuntime(
        {
            "success": False,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "completed": True,
            "process_id": "missing-proc",
            "error": "Process not found",
        }
    )
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    ctx = _context()
    result = await workspace_cli.write_stdin_internal(
        ctx,
        WriteStdinRequest(process_id="missing-proc", chars=""),
    )

    assert result.success is False
    assert result.completed is True
    assert result.process_id == "missing-proc"
    assert "not found" in (result.error or "").lower()
    assert runtime.session_id == f"shell-{ctx.conversation_id.hex}"


@pytest.mark.asyncio
async def test_write_stdin_setup_failure_preserves_process_binding(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeRuntime({})
    runtime.process_sessions["proc-1"] = "original-session"

    async def fail_get_session(**kwargs):
        del kwargs
        raise RuntimeError("agentbox manager returned 500")

    runtime.get_session = fail_get_session  # type: ignore[method-assign]
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    result = await workspace_cli.write_stdin_internal(
        _context(),
        WriteStdinRequest(process_id="proc-1", chars=""),
    )

    assert result.success is False
    assert result.completed is False
    assert result.process_id == "proc-1"
    assert runtime.process_sessions == {"proc-1": "original-session"}
    assert runtime.cleared_processes == []


@pytest.mark.asyncio
async def test_terminate_process_internal_routes_by_process_id(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeRuntime(
        {
            "success": True,
            "stdout": "stopped",
            "stderr": "",
            "exit_code": -15,
            "completed": True,
            "process_id": "proc-1",
        }
    )
    runtime.process_sessions["proc-1"] = "session-1"
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    result = await workspace_cli.terminate_process_internal(
        _context(),
        TerminateProcessRequest(process_id="proc-1"),
    )

    assert result.success is True
    assert result.completed is True
    assert runtime.session.last_terminate_process_id == "proc-1"
    assert runtime.cleared_processes == ["proc-1"]


@pytest.mark.asyncio
async def test_list_processes_internal_binds_running_processes(
    monkeypatch: pytest.MonkeyPatch,
):
    runtime = _FakeRuntime(
        {
            "processes": [
                {
                    "process_id": "proc-1",
                    "cmd": "npm run dev",
                    "cwd": "/workspace",
                    "tty": True,
                    "started_at": 123.0,
                    "completed": False,
                    "exit_code": None,
                }
            ]
        }
    )
    monkeypatch.setattr(
        workspace_cli,
        "get_workspace_tool_runtime",
        lambda: runtime,
    )

    result = await workspace_cli.list_processes_internal(
        _context(),
        ListProcessesRequest(),
    )

    assert result.success is True
    assert [process.process_id for process in result.processes] == ["proc-1"]
    assert runtime.bound_processes == [("proc-1", "session-1")]
