"""The `browser` tool: one `agent-browser` command, in the VM, never a shell.

The runtime is the seam, as it is for `exec_command`: a stand-in that records
which session was asked for and what ran in it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.tools.browser.vm_browser import (
    BrowserCommandRequest,
    browser_argv,
    browser_command_internal,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.file_access import _on_the_host
from app.modules.workspace.contracts.host_execution import HostWorkspace


class FakeSession:
    session_id = "shell"
    workspace_recreated = False

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def exec_command(self, *, cmd, **_):
        self.commands.append(cmd)
        return {
            "success": True,
            "exit_code": 0,
            "stdout": "opened",
            "stderr": "",
            "completed": True,
        }


class FakeRuntime:
    def __init__(self) -> None:
        self.session = FakeSession()
        self.vm_sessions = 0
        self.host_sessions = 0

    async def get_session(self, **_):
        self.vm_sessions += 1
        return self.session

    async def get_host_session(self, **_):
        self.host_sessions += 1
        return self.session


def _host_ctx() -> ConversationContext:
    return ConversationContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
        workspace_cwd="/home/user/lemma/c/2026-09-25/abc",
        host_workspace=HostWorkspace(sandbox_id=uuid4(), root="/Users/o/p"),
    )


async def test_it_runs_in_the_vm_even_on_a_host_run():
    runtime = FakeRuntime()

    result = await browser_command_internal(
        _host_ctx(),
        BrowserCommandRequest(args="open http://localhost:3000"),
        runtime=runtime,
    )

    assert result.success, result.error
    assert runtime.vm_sessions == 1 and runtime.host_sessions == 0
    assert runtime.session.commands == ["agent-browser open http://localhost:3000"]


@pytest.mark.parametrize(
    ("args", "argv"),
    [
        ("snapshot -i", ["agent-browser", "snapshot", "-i"]),
        ("agent-browser click @e3", ["agent-browser", "click", "@e3"]),
        ('fill @e5 "two words"', ["agent-browser", "fill", "@e5", "two words"]),
    ],
)
def test_arguments_are_split_like_a_command_line(args, argv):
    assert browser_argv(args) == argv


async def test_it_is_not_a_shell():
    runtime = FakeRuntime()
    await browser_command_internal(
        _host_ctx(),
        BrowserCommandRequest(args="open x; rm -rf ~ && echo pwned"),
        runtime=runtime,
    )
    (command,) = runtime.session.commands
    assert command == "agent-browser open 'x;' rm -rf '~' '&&' echo pwned"


@pytest.mark.parametrize("args", ["", "agent-browser", 'open "unterminated'])
async def test_bad_arguments_are_refused_before_anything_runs(args):
    runtime = FakeRuntime()
    result = await browser_command_internal(
        _host_ctx(), BrowserCommandRequest(args=args), runtime=runtime
    )
    assert not result.success and result.error
    assert runtime.session.commands == []


@pytest.mark.parametrize(
    ("path", "on_the_mac"),
    [
        ("/home/user/shots/page.png", False),
        ("/home/user", False),
        ("chart.png", True),
        ("/Users/o/p/out.png", True),
        ("/tmp/x.png", True),
    ],
)
def test_a_host_run_reads_images_from_the_mac_except_under_the_vm_home(
    path, on_the_mac
):
    assert _on_the_host(_host_ctx(), path) is on_the_mac


def test_a_vm_run_always_reads_from_the_vm():
    ctx = _host_ctx().model_copy(update={"host_workspace": None})
    assert _on_the_host(ctx, "chart.png") is False
