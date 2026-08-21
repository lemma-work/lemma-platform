"""What the shell and Python tools cost, and how they behave under pressure.

Real container, real processes, real PTYs. Two things are being pinned:

**Latency.** A command that prints nothing used to cost a full 29-second poll
window, because the sandbox signalled "finished" to whoever happened to be
waiting and a fast command exits before its first poll arrives. Three `mkdir`s
in a row was a minute and a half of an agent apparently doing nothing. These
tests assert wall-clock, because that is the property that broke and no
functional assertion notices it.

**The interactive path.** TTY allocation, a REPL driven across several calls,
control characters, resize, large pastes, and concurrent commands sharing one
workspace session — the things an agent actually does while writing code, and
the paths with the least coverage.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID, uuid4

import pytest
from fastapi import status

import app.modules.workspace.services.workspace_tool_runtime as workspace_runtime
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecutePythonRequest,
    ListProcessesRequest,
    ResizeTerminalRequest,
    TerminateProcessRequest,
    WriteStdinRequest,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
    execute_python_internal,
    list_processes_internal,
    resize_terminal_internal,
    terminate_process_internal,
    write_stdin_internal,
)
from app.modules.test_support.e2e.waiters import eventually

# asyncio, not anyio, like the other 139 e2e files. Neither of these two files
# uses anyio for anything -- no anyio API, no trio, no task groups -- but the
# marker made pytest-anyio run each test in its own fresh event loop while every
# fixture around them runs in the session loop pytest.ini configures. That is
# invisible until something is held across the boundary: a pooled Postgres
# connection opened during fixture setup and reused in the test body dies with
# "got Future attached to a different loop".
pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

# The window a silent command used to burn. Anything near it is the bug back.
# Deliberately far below 29s and far above a healthy round trip (~0.1-1s), so
# this neither flakes on a loaded machine nor passes with the regression.
_FAST_ENOUGH_SECONDS = 8.0


async def _agent_context(authenticated_client, fixed_test_org, fixed_test_user):
    await workspace_runtime.close_workspace_tool_runtimes()
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Shell Latency Pod {uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod = response.json()
    ctx = BaseAgentContext(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=UUID(pod["id"]),
        conversation_id=uuid4(),
        agent_name="shell_latency_e2e",
    )

    # Provision before measuring: a cold start is not what these tests are about,
    # and the first call against one correctly reports a retryable failure.
    async def _attempt_warmup():
        return await exec_command_internal(
            ctx,
            ExecCommandRequest(
                comment="warm the sandbox", cmd="true", timeout_seconds=180
            ),
        )

    await eventually(
        label="sandbox warmup",
        probe=_attempt_warmup,
        done=lambda warm: warm.success,
        timeout_seconds=30.0,
        interval_seconds=2.0,
    )
    return ctx


async def _timed(coro):
    started = time.monotonic()
    result = await coro
    return result, time.monotonic() - started


async def test_commands_that_print_nothing_return_immediately(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """The regression, in the shape an agent actually hits it.

    `mkdir`, `cd`, `touch` and most CLI calls print nothing and exit in
    milliseconds. Each one used to wait out the poll window, so a few lines of
    setup cost minutes.
    """
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    silent = [
        "mkdir -p /workspace/reports /workspace/research",
        "touch /workspace/reports/notes.md",
        "cd /workspace && true",
        "export BUILD_ENV=ci",
        "cp /workspace/reports/notes.md /workspace/reports/copy.md",
    ]

    total = 0.0
    for command in silent:
        result, elapsed = await _timed(
            exec_command_internal(
                ctx, ExecCommandRequest(comment="silent setup", cmd=command)
            )
        )
        assert result.success, result
        assert result.completed is True, result
        assert result.exit_code == 0, result
        assert elapsed < _FAST_ENOUGH_SECONDS, (
            f"{command!r} took {elapsed:.1f}s with no output to report -- the "
            "poll waited out its window instead of noticing the process had exited"
        )
        total += elapsed

    # The headline number: five setup commands should be seconds, not minutes.
    assert total < len(silent) * _FAST_ENOUGH_SECONDS, f"{total:.1f}s for five commands"


async def test_a_silent_python_snippet_returns_immediately(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """`execute_python` shares the same output path, so it shared the same bug."""
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    result, elapsed = await _timed(
        execute_python_internal(
            ctx,
            ExecutePythonRequest(
                comment="a snippet that prints nothing",
                code="x = sum(range(100))\n",
            ),
        )
    )
    assert result.success, result
    assert elapsed < _FAST_ENOUGH_SECONDS, f"silent python took {elapsed:.1f}s"


async def test_concurrent_commands_share_one_session_without_serialising(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """Agents issue tool calls in parallel; one workspace must serve them all.

    Four one-second sleeps: serialised that is 4s+, concurrent it is ~1s. The
    assertion is deliberately loose — the point is that they overlap at all, not
    that they are perfectly parallel.
    """
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    async def one(index: int):
        return await exec_command_internal(
            ctx,
            ExecCommandRequest(
                comment=f"parallel {index}",
                cmd=f"sleep 1; echo done-{index}",
                timeout_seconds=60,
            ),
        )

    started = time.monotonic()
    results = await asyncio.gather(*(one(i) for i in range(4)))
    elapsed = time.monotonic() - started

    for index, result in enumerate(results):
        assert result.success, result
        assert result.completed is True, result
        assert f"done-{index}" in (result.stdout or ""), result
    assert elapsed < 4.0, (
        f"four concurrent one-second commands took {elapsed:.1f}s -- they ran "
        "one after another"
    )


async def test_a_repl_can_be_driven_across_several_calls(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """The interactive contract: start, send, read, and exit cleanly.

    This is how an agent uses a debugger or a language REPL, and every step of
    it crosses the PTY path.
    """
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="open a python repl",
            cmd="python3 -q -u -i",
            tty=True,
            yield_time_ms=1500,
        ),
    )
    assert started.success, started
    assert started.completed is False and started.process_id, started

    first = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            comment="evaluate an expression",
            process_id=started.process_id,
            chars="print(6 * 7)\n",
            yield_time_ms=2000,
        ),
    )
    assert "42" in (first.stdout or ""), first
    assert first.completed is False, first

    # State survives between calls — that is the whole reason to hold a REPL.
    await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            process_id=started.process_id, chars="answer = 99\n", yield_time_ms=1500
        ),
    )
    recalled = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            process_id=started.process_id, chars="print(answer)\n", yield_time_ms=2000
        ),
    )
    assert "99" in (recalled.stdout or ""), recalled

    # Ctrl-D leaves the REPL, and the tool must notice it ended.
    closed = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            comment="send EOF",
            process_id=started.process_id,
            chars="",
            yield_time_ms=3000,
        ),
    )
    assert closed.completed is True, closed


async def test_ctrl_c_interrupts_a_running_command_without_killing_the_shell(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """Interrupting beats abandoning — the prompt docs tell the agent so."""
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    shell = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="an interactive shell",
            cmd="sh -i",
            tty=True,
            yield_time_ms=1500,
        ),
    )
    assert shell.process_id, shell

    await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            process_id=shell.process_id,
            chars="sleep 300\n",
            yield_time_ms=1000,
        ),
    )
    interrupted = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            comment="Ctrl-C",
            process_id=shell.process_id,
            chars="",
            yield_time_ms=1500,
        ),
    )
    assert interrupted.completed is False, "Ctrl-C must not kill the shell itself"

    alive = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            process_id=shell.process_id,
            chars="echo still-here\n",
            yield_time_ms=2000,
        ),
    )
    assert "still-here" in (alive.stdout or ""), alive


async def test_a_large_paste_into_a_tty_is_delivered_whole(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """A PTY master is a non-blocking fd with a small kernel buffer.

    An agent pasting a file into `cat > file` writes far more than that buffer
    holds, so the write has to be drained rather than attempted once.
    """
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="read a heredoc from stdin",
            cmd="cat > /workspace/pasted.txt",
            tty=True,
            yield_time_ms=800,
        ),
    )
    assert started.process_id, started

    line = "x" * 199 + "\n"
    payload = line * 120  # ~24KB, several times a typical PTY buffer
    pasted = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            comment="paste a large block",
            process_id=started.process_id,
            chars=payload,
            yield_time_ms=1500,
        ),
    )
    # Asserted explicitly: the original bug reported failure here and the test
    # that missed it went on to count lines, blaming the wrong thing.
    assert pasted.success, pasted
    await write_stdin_internal(
        ctx,
        WriteStdinRequest(process_id=started.process_id, chars="", yield_time_ms=2000),
    )

    # A PTY echoes and translates newlines, so compare the line count rather
    # than the bytes: what matters is that nothing was dropped.
    check = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="confirm the paste landed",
            cmd="grep -c '^x\\{199\\}' /workspace/pasted.txt",
        ),
    )
    assert check.exit_code == 0, check
    assert int((check.stdout or "0").strip() or 0) >= 120, check.stdout


async def test_resize_terminal_changes_the_pty_window_size(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """`resize_terminal` is a wired tool with no e2e coverage anywhere.

    A successful response is not enough to trust it: assert the PTY the shell
    is attached to actually reports the new size, the way an agent would
    verify a resize before rereading a clipped full-screen program.
    """
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    shell = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="an interactive shell to resize",
            cmd="sh -i",
            tty=True,
            yield_time_ms=1000,
        ),
    )
    assert shell.process_id, shell
    assert shell.completed is False, shell

    resized = await resize_terminal_internal(
        ctx,
        ResizeTerminalRequest(
            comment="widen and heighten the terminal",
            process_id=shell.process_id,
            cols=200,
            rows=50,
        ),
    )
    assert resized.success, resized
    assert resized.completed is False, resized
    assert resized.process_id == shell.process_id, resized

    reported = await write_stdin_internal(
        ctx,
        WriteStdinRequest(
            comment="read back the pty size",
            process_id=shell.process_id,
            chars="stty size\n",
            yield_time_ms=1500,
        ),
    )
    # `stty size` prints "<rows> <cols>".
    assert "50 200" in (reported.stdout or ""), reported


async def test_resize_terminal_on_an_unknown_process_fails_without_raising(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """The control-tool guard shared with terminate must cover resize too."""
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    resized = await resize_terminal_internal(
        ctx,
        ResizeTerminalRequest(
            comment="resize a process that was never started",
            process_id=f"never-started-{uuid4().hex[:8]}",
            cols=80,
            rows=24,
        ),
    )
    assert resized.success is False, resized
    assert resized.error, resized


async def test_a_process_survives_being_listed_and_can_be_terminated(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    configure_workspace_api_url,
):
    """Recovering a handle and stopping the process are the two escape hatches
    an agent has when a command outlives its call."""
    del configure_workspace_api_url
    ctx = await _agent_context(authenticated_client, fixed_test_org, fixed_test_user)

    started = await exec_command_internal(
        ctx,
        ExecCommandRequest(
            comment="a server that never exits",
            cmd="python3 -c 'import time; print(\"ready\", flush=True); time.sleep(600)'",
            timeout_seconds=10,
        ),
    )
    assert started.completed is False and started.process_id, started

    listed = await list_processes_internal(ctx, ListProcessesRequest())
    assert any(
        process.process_id == started.process_id and not process.completed
        for process in listed.processes
    ), listed

    stopped, elapsed = await _timed(
        terminate_process_internal(
            ctx,
            TerminateProcessRequest(comment="stop it", process_id=started.process_id),
        )
    )
    assert stopped.success, stopped
    assert elapsed < _FAST_ENOUGH_SECONDS, f"terminate took {elapsed:.1f}s"

    after = await list_processes_internal(ctx, ListProcessesRequest())
    live = [
        process
        for process in after.processes
        if process.process_id == started.process_id and not process.completed
    ]
    assert not live, after
