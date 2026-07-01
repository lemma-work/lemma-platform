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

    Use this for repo inspection, builds, tests, and file edits through standard CLI commands.
    The workspace injects Lemma environment variables for the current user/pod, so
    `lemma ...` CLI commands may be used for pod operations when the task calls for
    them. Do not use raw localhost probes to diagnose host Lemma API/Auth
    availability: the command runs inside an isolated workspace, so `localhost` is
    the workspace container, not the host backend.
    The workspace is a sandbox: files created here are not directly visible to the
    user. Upload final deliverables to pod files under `/me/...` with `lemma files
    upload` before presenting or referencing them as user-accessible files.

    Modes:
    - One-shot (`tty=false`, default): returns after a short initial wait. Quick commands complete normally;
      long-running commands return `process_id` instead of blocking the agent.
    - Interactive (`tty=true`): starts a real TTY terminal process and returns `process_id` for follow-up input.
    - Commands run in the workspace container directly; no sandbox/escalation fields are used here.

    Interactive workflow (for commands like `npm run dev`):
    1) Start with `tty=true`:
       `{"cmd":"npm run dev","tty":true,"yield_time_ms":1000}`
    2) Continue with `manage_process` using the returned `process_id`:
       - poll output: `{"action":"input","process_id":"...","chars":"","yield_time_ms":1000}`
       - send input: `{"action":"input","process_id":"...","chars":"q\\n"}`
       - stop it: `{"action":"kill","process_id":"..."}`

    Use `manage_process` with `{"action":"list"}` before starting another
    long-running server or when you need to find a process started earlier.

    Editing files via CLI example:
    - Overwrite file:
      `{"cmd":"cat > src/config.json <<'EOF'\\n{\\\"mode\\\":\\\"dev\\\"}\\nEOF"}`
    - Append line:
      `{"cmd":"echo 'export DEBUG=1' >> .env.local"}`
    """
    return await workspace_cli.exec_command(ctx.deps, request)


async def manage_process(
    ctx: RunContext[BaseAgentContext],
    request: ManageProcessRequest,
) -> Any:
    """
    Drive a process started by `exec_command` (interactive or long-running).

    One tool, three actions:
    - `action="input"` — send characters to (or just poll output from) a running
      process. Needs `process_id`. Poll logs with `chars=""`; respond to a prompt
      with `chars="y\\n"`; run another command in the same shell with
      `chars="npm test\\n"`.
    - `action="kill"` — stop a process by `process_id` (servers, REPLs, watchers,
      or anything started accidentally).
    - `action="list"` — list tracked shell processes in this workspace (find dev
      servers/REPLs before polling, stopping, or starting another).
    """
    if request.action == "list":
        return await workspace_cli.list_processes(
            ctx.deps, ListProcessesRequest(comment=request.comment)
        )
    if not request.process_id:
        return ExecCommandResult(
            success=False,
            completed=False,
            error="process_id is required for action='input' and action='kill'.",
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
    Execute Python code in the shared conversation-scoped IPython kernel.

    Use this for structured data analysis, transformations, parsing, and calculations
    that are awkward in pure shell commands. Put the entire code snippet in
    `request.code`. The kernel state persists across calls in the same conversation session.
    Variables, imports, and in-memory objects from earlier executions remain available
    for later executions, so use it for stepwise analysis when helpful.
    Include a short `request.comment` to show the user-facing intent.
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
