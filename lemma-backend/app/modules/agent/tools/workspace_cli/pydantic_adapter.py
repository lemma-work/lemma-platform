from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecCommandResult,
    ExecutePythonRequest,
    ListProcessesRequest,
    ManageProcessRequest,
    ResizeTerminalRequest,
    TerminateProcessRequest,
    ViewImageRequest,
    WriteStdinRequest,
)


async def exec_command(
    ctx: RunContext[BaseAgentContext],
    request: ExecCommandRequest,
) -> ExecCommandResult:
    """
    Run a shell command in the private conversation workspace.

    Use it for repo inspection, builds, tests, file edits, and `lemma` CLI calls
    (pod credentials are pre-injected). `localhost` is this container, not the
    Lemma backend.

    Default (`tty=false`) returns after a short wait; a command still running
    returns a `process_id` instead of blocking. `tty=true` starts a real terminal
    for interactive commands. Either way, drive the process afterwards with
    `manage_process` — poll or send input with `action="input"`, stop it with
    `action="kill"`, and use `action="list"` to find processes started earlier.

    Long commands (installs, builds, test suites) are normal and supported. When
    one outlives the wait window you get `completed: false` plus a `process_id`,
    and the command carries on running — nothing was cancelled and no output is
    lost. Keep polling until it finishes:

        exec_command(cmd="npm ci && npm run build", timeout_seconds=300)
        -> completed: false, process_id: "abc"
        manage_process(action="input", process_id="abc", chars="")
        -> completed: false        # repeat; each poll returns new output
        manage_process(action="input", process_id="abc", chars="")
        -> completed: true, exit_code: 0

    Never re-run a command because it did not finish — that starts a second
    build alongside the first. If you lose a `process_id`, `action="list"`
    recovers it.
    """
    return await workspace_cli.exec_command(ctx.deps, request)


async def manage_process(
    ctx: RunContext[BaseAgentContext],
    request: ManageProcessRequest,
) -> Any:
    """
    Drive a process started by `exec_command`.

    `input` sends characters to a running process, or polls its output when
    `chars=""`. `kill` stops it. `list` shows tracked processes in this
    workspace. `resize` changes an interactive terminal's `cols`/`rows`. All but
    `list` need `process_id`.
    """
    if request.action == "list":
        return await workspace_cli.list_processes(
            ctx.deps, ListProcessesRequest(comment=request.comment)
        )
    if not request.process_id:
        return ExecCommandResult(
            success=False,
            completed=False,
            error=(
                "process_id is required for action='input', 'kill', and 'resize'."
            ),
        )
    if request.action == "resize":
        return await workspace_cli.resize_terminal(
            ctx.deps,
            ResizeTerminalRequest(
                process_id=request.process_id,
                cols=request.cols,
                rows=request.rows,
                comment=request.comment,
            ),
        )
    if request.action == "kill":
        return await workspace_cli.terminate_process(
            ctx.deps,
            TerminateProcessRequest(
                process_id=request.process_id, comment=request.comment
            ),
        )
    # action == "input"
    return await workspace_cli.write_stdin(
        ctx.deps,
        WriteStdinRequest(
            process_id=request.process_id,
            chars=request.chars,
            max_output_tokens=request.max_output_tokens,
            yield_time_ms=request.yield_time_ms,
            comment=request.comment,
        ),
    )


async def execute_python(
    ctx: RunContext[BaseAgentContext],
    request: ExecutePythonRequest,
) -> Any:
    """
    Run Python in the conversation's shared IPython kernel.

    Use it for data analysis, transformations, and calculations that are awkward
    in shell. Kernel state — imports, variables, objects — persists across calls,
    so build up an analysis stepwise instead of repeating setup.
    """
    return await workspace_cli.execute_python(ctx.deps, request)


async def view_image(
    ctx: RunContext[BaseAgentContext],
    request: ViewImageRequest,
) -> Any:
    """
    Load an image file from the private workspace and return it as binary tool content.

    Use this for screenshots, generated images, charts, or any other visual artifact
    that the agent should inspect.

    PATH HANDLING:
    - This tool reads only from the private current conversation workspace directory.
    - Always pass a relative path such as `images/output.png`.
    - Do not pass absolute paths or paths outside the current workspace directory.
    """
    return await workspace_cli.view_image(ctx.deps, request)


_WORKSPACE_CLI_BASE_TOOLS = [
    exec_command,
    manage_process,
    execute_python,
]

workspace_cli_toolset = FunctionToolset[BaseAgentContext](
    tools=list(_WORKSPACE_CLI_BASE_TOOLS)
)

# `view_image` lives in its own always-on-when-supported toolset (see
# `registry.py`'s VIEW_IMAGE toolset) rather than workspace_cli, so it reaches
# any agent with a vision-capable model regardless of configured toolsets.
view_image_toolset = FunctionToolset[BaseAgentContext](tools=[view_image])


def is_workspace_cli_toolset(toolset: object) -> bool:
    """True for the workspace CLI toolset (the capability assembler keys its
    usage-prompt special handling off this identity check)."""
    return toolset is workspace_cli_toolset
