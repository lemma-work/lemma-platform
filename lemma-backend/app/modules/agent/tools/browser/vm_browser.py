"""`browser`: drive the VM browser when this run's shell is somewhere else.

The browser the person watches is Chrome in their VM workspace, and it is
driven with the `agent-browser` CLI. Ordinarily that is just a command line
the agent runs through `exec_command`, which is why no typed browser tools
exist. Host execution (docs/architecture/desktop-host-execution.md) breaks
that assumption: the run's shell is on the owner's Mac -- `exec_command` runs
there, or, for a coding agent, Lemma's shell is withheld entirely -- and
`agent-browser` exists only in the VM.

So this one tool runs exactly one `agent-browser` invocation in the owner's VM
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

from app.modules.agent.tools.context import TOOL_COMMENT_DESC, BaseAgentContext
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecCommandResult,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import (
    exec_command_internal,
)

AGENT_BROWSER = "agent-browser"


class BrowserCommandRequest(BaseModel):
    args: str = Field(
        description=(
            "The `agent-browser` arguments, as on its command line: "
            "`open https://example.com`, `snapshot -i`, `click @e3`, "
            "`screenshot /tmp/page.png`."
        )
    )
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=10,
        le=300,
        description="How long to wait for the command, instead of the default 30s.",
    )


def browser_argv(args: str) -> list[str]:
    """The `agent-browser` command line these arguments make. Raises ValueError."""
    argv = shlex.split(args)
    if argv and argv[0] == AGENT_BROWSER:
        argv = argv[1:]
    if not argv:
        raise ValueError("give the agent-browser arguments, e.g. `open <url>`")
    return [AGENT_BROWSER, *argv]


async def _no_project(*_args: object, **_kwargs: object) -> None:
    """A browser command needs no repository cloned for it."""


async def browser_command_internal(
    ctx: BaseAgentContext, request: BrowserCommandRequest, *, runtime=None
) -> ExecCommandResult:
    try:
        argv = browser_argv(request.args)
    except ValueError as exc:
        return ExecCommandResult(success=False, error=f"Invalid arguments: {exc}")
    # The VM workspace, whatever this run's own shell is: dropping the host
    # workspace from a copy of the context is what routes it there.
    vm_ctx = ctx.model_copy(update={"host_workspace": None})
    return await exec_command_internal(
        vm_ctx,
        ExecCommandRequest(
            cmd=shlex.join(argv),
            comment=request.comment,
            timeout_seconds=request.timeout_seconds,
        ),
        runtime=runtime,
        prepare_project=_no_project,
    )


async def browser(
    ctx: RunContext[BaseAgentContext],
    request: BrowserCommandRequest,
) -> ExecCommandResult:
    """
    Drive the browser the person watches in Lemma, with `agent-browser`.

    That browser runs in Lemma's VM, not on this Mac, and this is the only way
    to reach it from here: one `agent-browser` command per call, e.g.
    `open https://localhost:3000`, `snapshot -i`, `click @e2`,
    `fill @e5 "text"`, `screenshot /tmp/shot.png`. Load the `browser` skill for
    the full command set. A screenshot is saved in the VM; look at it with
    `view_image(workspace_file_path=...)`. The VM reaches this Mac's
    `localhost`, so a dev server you started here opens at the same URL.

    Not a shell: only `agent-browser` runs, and pipes or `&&` are passed to it
    as arguments.
    """
    return await browser_command_internal(ctx.deps, request)


vm_browser_toolset = FunctionToolset[BaseAgentContext](tools=[browser])


def is_vm_browser_toolset(toolset: object) -> bool:
    return toolset is vm_browser_toolset
