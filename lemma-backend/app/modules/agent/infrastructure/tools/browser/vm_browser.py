"""`browser`: drive the VM browser when this run's shell is somewhere else.

The browser the person watches is Chrome in their VM workspace, and it is
driven with the `agent-browser` CLI. Ordinarily that is just a command line
the agent runs through `exec_command`, which is why no typed browser tools
exist. Host execution (docs/architecture/desktop-host-execution.md) breaks
that assumption: the run's shell is on the user's Mac -- `exec_command` runs
there, or, for a coding agent, Lemma's shell is withheld entirely -- and
`agent-browser` exists only in the VM.

So this one tool runs exactly one `agent-browser` invocation in the user's VM
workspace -- the sandbox that keeps its own id and holds the browser -- with
the same session, clocks and output shaping `exec_command` gives. It is not a
shell: the arguments are split and re-quoted, so nothing but `agent-browser`
can run through it.

Offered only on runs whose commands execute on the host, and only to agents
that could drive the browser before (they have the workspace CLI); see
`RunToolAssembler`.
"""

from __future__ import annotations

import shlex
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.infrastructure.tools.responses import TOOL_COMMENT_DESC
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecCommandResult,
)
from app.modules.agent.infrastructure.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
)

AGENT_BROWSER = "agent-browser"

# The lock `save-webpage` holds for the length of one capture. Every command of
# a capture acts on whatever tab the shared session is bound to *now*, so a
# command from here landing mid-capture switched the tab under it, and the
# capture saved the wrong page under the right name.
_CAPTURE_LOCK = "/tmp/lemma-capture.lock"
_CAPTURE_LOCK_WAIT_SECONDS = 45
_LOCK_BUSY_EXIT = 75
_DEFAULT_TIMEOUT_SECONDS = 60


class BrowserCommandRequest(BaseModel):
    args: str = Field(
        description=(
            "The `agent-browser` arguments, as on its command line: "
            "`open https://example.com`, `snapshot -i`, `click @e3`, "
            "`screenshot /home/user/shots/page.png`."
        )
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=10,
        le=600,
        description=(
            "How long to wait for the command, instead of the default "
            f"{_DEFAULT_TIMEOUT_SECONDS}s."
        ),
    )


def browser_argv(args: str) -> list[str]:
    """The `agent-browser` command line these arguments make. Raises ValueError."""
    argv = shlex.split(args)
    if argv and argv[0] == AGENT_BROWSER:
        argv = argv[1:]
    if not argv:
        raise ValueError("give the agent-browser arguments, e.g. `open <url>`")
    return [AGENT_BROWSER, *argv]


def locked_command(argv: list[str], *, timeout_seconds: int | None) -> str:
    """``argv`` run while holding the capture lock.

    The wait stays inside this call's own patience: a command still queued when
    the call gave up would otherwise run later, unasked -- a click landing
    after the agent had moved on. ``-o`` keeps the lock from leaking into a
    daemon the command starts, which is what once held it for the life of a
    sandbox.
    """
    patience = timeout_seconds or _DEFAULT_TIMEOUT_SECONDS
    wait = max(1, min(_CAPTURE_LOCK_WAIT_SECONDS, patience - 10))
    return shlex.join(
        [
            "flock",
            "-o",
            "-w",
            str(wait),
            "-E",
            str(_LOCK_BUSY_EXIT),
            _CAPTURE_LOCK,
            *argv,
        ]
    )


async def _no_project(*_args: object, **_kwargs: object) -> None:
    """A browser command needs no repository cloned for it."""


async def browser_command_internal(
    ctx: ToolContext, request: BrowserCommandRequest, *, runtime=None
) -> ExecCommandResult:
    try:
        argv = browser_argv(request.args)
    except ValueError as exc:
        return ExecCommandResult(success=False, error=f"Invalid arguments: {exc}")
    # The VM workspace, whatever this run's own shell is: dropping the host
    # workspace from a copy of the context is what routes it there.
    vm_ctx = ctx.model_copy(update={"host_workspace": None})
    result = await exec_command_internal(
        vm_ctx,
        ExecCommandRequest(
            cmd=locked_command(argv, timeout_seconds=request.timeout_seconds),
            comment=request.comment,
            timeout_seconds=request.timeout_seconds,
        ),
        runtime=runtime,
        prepare_project=_no_project,
    )
    if result.exit_code == _LOCK_BUSY_EXIT and not (result.stdout or "").strip():
        return result.model_copy(
            update={
                "success": False,
                "error": (
                    "The browser is busy saving a page for `web_fetch`, so this "
                    "command was not run. Try it again in a moment."
                ),
            }
        )
    return result


async def browser(
    ctx: RunContext[ToolContext],
    request: BrowserCommandRequest,
) -> ExecCommandResult:
    """
    Drive the browser the person watches in Lemma, with `agent-browser`.

    That browser runs in Lemma's VM, not on this Mac, and this is the only way
    to reach it from here: one `agent-browser` command per call, e.g.
    `open https://localhost:3000`, `snapshot -i`, `click @e2`,
    `fill @e5 "text"`, `screenshot /home/user/shots/shot.png`. Load the
    `browser` skill for the full command set. A screenshot is saved in the VM:
    give it an absolute path under `/home/user/` -- there is no shell here to
    expand `$PWD`, and `view_image` reads any other path from this Mac -- then
    look at it with `view_image(workspace_file_path=...)`. The VM reaches this
    Mac's `localhost`, so a dev server you started here opens at the same URL.

    Not a shell: only `agent-browser` runs, and pipes or `&&` are passed to it
    as arguments.
    """
    return await browser_command_internal(ctx.deps, request)


vm_browser_toolset = FunctionToolset[ToolContext](tools=[browser])


def is_vm_browser_toolset(toolset: object) -> bool:
    return toolset is vm_browser_toolset
