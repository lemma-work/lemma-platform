from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import helper as workspace_helper
from app.modules.agent.tools.workspace_cli import process_visibility, workspace_cli
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
        raise RuntimeError("sandbox runtime returned 500")


class _FakeWorkspaceSession:
    def __init__(self, result: dict, *, session_id: str = "session-1"):
        self.result = result
        self.session_id = session_id
        self.auto_close = False
        self.deleted = False
        self.workspace_recreated = False

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
    assert "sandbox runtime returned 500" in (result.error or "")
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
        raise RuntimeError("sandbox runtime returned 500")

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
                    "cwd": "",
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


def _process(process_id: str, cwd: str = "") -> dict:
    return {
        "process_id": process_id,
        "cmd": "npm run dev",
        # Empty unless a test is about directories: an entry the provider
        # recorded no directory for stays visible, so these keep testing the
        # session binding they were written for.
        "cwd": cwd,
        "tty": True,
        "started_at": 123.0,
        "completed": False,
        "exit_code": None,
    }


@pytest.mark.asyncio
async def test_list_processes_internal_hides_another_conversations_processes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Listing processes must not capture a sub-agent's running process.

    One sandbox serves every conversation a user has, so this list spans all of
    them. Rebinding indiscriminately let whichever agent listed last take over
    processes started by its own sub-agents, leaving the real owner unable to
    drive or terminate them.
    """

    runtime = _FakeRuntime({"processes": [_process("mine"), _process("theirs")]})
    runtime.process_sessions["theirs"] = "session-other"
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
    assert [process.process_id for process in result.processes] == ["mine"]
    # The unowned process is claimed; the one another session owns is untouched.
    assert runtime.bound_processes == [("mine", "session-1")]
    assert runtime.process_sessions["theirs"] == "session-other"


class TestCommandOutputIsAlwaysBounded:
    """One `npm ci` must not cost a conversation its context.

    Only the PTY path was capped, and `tty` defaults to False -- so
    `exec_command`, the tool an agent reaches for most often, was the uncapped
    one. A large build log landed whole in the tool result, was persisted, and
    was replayed on every subsequent turn of that conversation.
    """

    def _rendered(self, *, tty: bool, stdout: str = "", stderr: str = ""):
        return workspace_helper.render_terminal_result(
            {"stdout": stdout, "stderr": stderr}, tty=tty
        )

    def test_the_default_non_tty_path_is_capped(self) -> None:
        from app.modules.agent.tools.workspace_cli.helper import (
            CHARACTER_LIMIT_STDOUT,
        )

        stdout, _ = self._rendered(tty=False, stdout="x" * (CHARACTER_LIMIT_STDOUT * 3))

        assert stdout is not None
        assert len(stdout) < CHARACTER_LIMIT_STDOUT * 2

    def test_truncation_says_so(self) -> None:
        """Silently keeping the tail reads as a complete log, so an agent
        reports 'no errors' for a build whose errors scrolled off."""
        from app.modules.agent.tools.workspace_cli.helper import (
            CHARACTER_LIMIT_STDOUT,
        )

        stdout, _ = self._rendered(tty=False, stdout="x" * (CHARACTER_LIMIT_STDOUT * 3))

        assert "truncated" in stdout

    def test_the_end_is_what_survives(self) -> None:
        """A build's errors are at the bottom."""
        from app.modules.agent.tools.workspace_cli.helper import (
            CHARACTER_LIMIT_STDOUT,
        )

        noise = "warning\n" * CHARACTER_LIMIT_STDOUT
        stdout, _ = self._rendered(tty=False, stdout=noise + "FATAL: build failed")

        assert "FATAL: build failed" in stdout

    def test_stderr_gets_its_own_smaller_limit(self) -> None:
        """`CHARACTER_LIMIT_STDERR` existed and this path passed the stdout one."""
        from app.modules.agent.tools.workspace_cli.helper import (
            CHARACTER_LIMIT_STDERR,
            CHARACTER_LIMIT_STDOUT,
        )

        _, stderr = self._rendered(tty=True, stderr="e" * (CHARACTER_LIMIT_STDOUT * 2))

        assert stderr is not None
        assert len(stderr) < CHARACTER_LIMIT_STDERR * 2

    def test_output_under_the_cap_is_untouched(self) -> None:
        stdout, stderr = self._rendered(tty=False, stdout="all good", stderr="")

        assert stdout == "all good"
        assert stderr == ""


@pytest.mark.asyncio
async def test_list_processes_hides_finished_processes_nobody_owns(
    monkeypatch: pytest.MonkeyPatch,
):
    """The stale-process complaint, in one case.

    One sandbox serves every conversation a user has. Bindings are cleared when
    a process completes, and nothing prunes the process index, so every command
    any of that user's conversations had ever finished arrived here unowned --
    and unowned was enough to be listed. An agent asking which processes exist
    got a growing list of other conversations' corpses, carrying no command
    line to tell them apart, and the tool docstring points it at that list to
    recover a process id.
    """
    finished = _process("proc-old")
    finished["completed"] = True
    finished["exit_code"] = 0
    runtime = _FakeRuntime({"processes": [finished, _process("proc-live")]})
    monkeypatch.setattr(workspace_cli, "get_workspace_tool_runtime", lambda: runtime)

    result = await workspace_cli.list_processes_internal(
        _context(),
        ListProcessesRequest(),
    )

    assert [process.process_id for process in result.processes] == ["proc-live"]
    # And it is not adopted on the way past: binding a dead process to this
    # conversation is how a corpse becomes this agent's problem.
    assert runtime.bound_processes == [("proc-live", "session-1")]


@pytest.mark.asyncio
async def test_a_finished_process_this_session_owns_is_still_listed(
    monkeypatch: pytest.MonkeyPatch,
):
    """Only *unowned* corpses are hidden -- an agent must still be able to read
    the outcome of the command it started itself."""
    finished = _process("proc-mine")
    finished["completed"] = True
    finished["exit_code"] = 0
    runtime = _FakeRuntime({"processes": [finished]})
    runtime.process_sessions = {"proc-mine": "session-1"}
    monkeypatch.setattr(workspace_cli, "get_workspace_tool_runtime", lambda: runtime)

    result = await workspace_cli.list_processes_internal(
        _context(),
        ListProcessesRequest(),
    )

    assert [process.process_id for process in result.processes] == ["proc-mine"]


@pytest.mark.asyncio
async def test_list_processes_hides_another_directorys_running_process(
    monkeypatch: pytest.MonkeyPatch,
):
    """A sandbox belongs to the user, not to one conversation.

    Several conversations can be working in it at once, each in its own
    directory. A running process nobody currently owns used to be shown to
    whichever of them listed first -- and adopted by it, so a sibling
    conversation's build became this agent's to poll and kill. The directory is
    what tells them apart once a session binding is gone.
    """
    ctx = _context()
    own_cwd = ctx.get_workspace_cwd()
    runtime = _FakeRuntime(
        {
            "processes": [
                _process("proc-theirs", cwd="/workspace/conversations/someone-else"),
                _process("proc-mine", cwd=own_cwd),
            ]
        }
    )
    monkeypatch.setattr(workspace_cli, "get_workspace_tool_runtime", lambda: runtime)

    result = await workspace_cli.list_processes_internal(ctx, ListProcessesRequest())

    assert [process.process_id for process in result.processes] == ["proc-mine"]
    assert runtime.bound_processes == [("proc-mine", "session-1")]


@pytest.mark.asyncio
async def test_a_process_in_a_subdirectory_of_ours_is_ours(
    monkeypatch: pytest.MonkeyPatch,
):
    ctx = _context()
    runtime = _FakeRuntime(
        {"processes": [_process("proc-sub", cwd=f"{ctx.get_workspace_cwd()}/video")]}
    )
    monkeypatch.setattr(workspace_cli, "get_workspace_tool_runtime", lambda: runtime)

    result = await workspace_cli.list_processes_internal(ctx, ListProcessesRequest())

    assert [process.process_id for process in result.processes] == ["proc-sub"]


def test_a_process_with_no_recorded_directory_stays_visible() -> None:
    """Older entries carry no directory, and the in-sandbox runtime reports
    none. Excluding those would drop a live process out of the only listing
    that can return its id."""
    assert process_visibility.within("", "/workspace/conversations/a") is True
    assert process_visibility.within(None, "/workspace/conversations/a") is True
    # And a prefix that is not a path boundary is not a match.
    assert (
        process_visibility.within(
            "/workspace/conversations/ab", "/workspace/conversations/a"
        )
        is False
    )
