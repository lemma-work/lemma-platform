from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.workspace_cli import machine_tools, workspace_cli
from app.modules.agent.tools.workspace_cli.machine_tools import (
    ExposePortRequest,
    ListMachinesRequest,
    ResetMachineRequest,
    SyncFilesRequest,
)
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
    Run a shell command on your machine, in this conversation's directory.

    Use it for repo inspection, builds, tests, file edits, and `lemma` CLI calls
    (pod credentials are pre-injected). `localhost` is the machine, not the
    Lemma backend. Name `machine` to run on another one you may use (see
    `list_machines`); on a pod machine each run gets its own directory.

    Default (`tty=false`) returns after a short wait; a command still running
    returns a `process_id` instead of blocking. `tty=true` starts a real terminal
    for interactive commands. Either way, drive the process afterwards with
    `manage_process` — send input or read new output with `action="input"`, stop
    it with `action="kill"`, and use `action="list"` to find processes started
    earlier. To wait for one to finish, use `wait_for`.

    Long commands (installs, builds, test suites) are normal and supported. When
    one outlives the wait window you get `completed: false` plus a `process_id`,
    and the command carries on running — nothing was cancelled and no output is
    lost. Hand that id to `wait_for` and stop:

        exec_command(cmd="npm ci && npm run build")
        -> completed: false, process_id: "abc"
        wait_for(reason="the build", process_id="abc")
        -> woke_because: "TARGET_FINISHED", exit_code: 0

    `wait_for` ends your turn and gives you a new one when the process exits, so
    a ten-minute build costs one model call rather than twenty. Use
    `manage_process` to send input to a process, to stop one, or to look at a
    running one — not to wait for it.

    Never re-run a command because it did not finish — that starts a second
    build alongside the first. If you lose a `process_id`, `action="list"`
    recovers it.
    """
    return await workspace_cli.exec_command_internal(ctx.deps, request)


async def manage_process(
    ctx: RunContext[BaseAgentContext],
    request: ManageProcessRequest,
) -> Any:
    """
    Drive a process started by `exec_command`.

    `input` sends characters to a running process, or polls its output when
    `chars` is omitted. `kill` stops it. `list` shows tracked processes on the
    machine (`machine` picks another). `resize` changes an interactive
    terminal's `cols`/`rows`. All but `list` need `process_id`.
    """
    if request.action == "list":
        return await workspace_cli.list_processes_internal(
            ctx.deps,
            ListProcessesRequest(comment=request.comment, machine=request.machine),
        )
    if not request.process_id:
        return ExecCommandResult(
            success=False,
            completed=False,
            error=("process_id is required for action='input', 'kill', and 'resize'."),
        )
    if request.action == "resize":
        return await workspace_cli.resize_terminal_internal(
            ctx.deps,
            ResizeTerminalRequest(
                process_id=request.process_id,
                cols=request.cols,
                rows=request.rows,
                comment=request.comment,
            ),
        )
    if request.action == "kill":
        return await workspace_cli.terminate_process_internal(
            ctx.deps,
            TerminateProcessRequest(
                process_id=request.process_id, comment=request.comment
            ),
        )
    # action == "input"
    return await workspace_cli.write_stdin_internal(
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
    return await workspace_cli.execute_python_internal(ctx.deps, request)


async def view_image(
    ctx: RunContext[BaseAgentContext],
    request: ViewImageRequest,
) -> Any:
    """
    Load an image and return it as binary tool content, from either store.

    Use this for screenshots, generated images, charts, or any other visual artifact
    that the agent should inspect.

    PATH HANDLING:
    - Set exactly one of `workspace_file_path` or `pod_file_path`. Setting both,
      or neither, is refused — the store is the one you name, never inferred
      from the shape of the path.
    - `workspace_file_path` is relative to the conversation's directory on your
      machine, e.g. `images/output.png`. Use it for artifacts this run just
      produced.
    - `pod_file_path` is a datastore path, e.g. `/me/photo.jpg`. Use it for a
      file that lives in the pod.
    - Images only. For a PDF, use `pod_view_document_pages` instead.
    """
    return await workspace_cli.view_image_internal(ctx.deps, request)


async def list_machines(
    ctx: RunContext[BaseAgentContext],
    request: ListMachinesRequest,
) -> machine_tools.ListMachinesResult:
    """
    The machines you may run commands on: your usual one, the pod's shared
    machines you are allowed, with each one's size, whether it has Docker,
    and its status. Pass a `ref` as `machine` to `exec_command` and the other
    machine tools.
    """
    return await machine_tools.list_machines_internal(ctx.deps, request)


async def expose_port(
    ctx: RunContext[BaseAgentContext],
    request: ExposePortRequest,
) -> machine_tools.ExposePortResult:
    """
    Turn a port on the machine into a preview URL people can open, e.g. a dev
    server you started (`npm run dev` on 5173). The URL is its own origin, so
    hot reload and root-absolute assets work. `private` previews ask the person
    to be signed in to Lemma; `link` previews work for anyone for at most 24
    hours. Reply with the URL; the person can revoke it.
    """
    return await machine_tools.expose_port_internal(ctx.deps, request)


async def sync_files(
    ctx: RunContext[BaseAgentContext],
    request: SyncFilesRequest,
) -> machine_tools.SyncFilesResult:
    """
    Copy files between the machine and the pod's files without downloading
    them yourself: `to_pod` puts a file or a folder's files into a pod folder
    (what you made, for people to see); `from_pod` brings one pod file onto
    the machine.
    """
    return await machine_tools.sync_files_internal(ctx.deps, request)


async def reset_machine(
    ctx: RunContext[BaseAgentContext],
    request: ResetMachineRequest,
) -> machine_tools.ResetMachineResult:
    """
    Free a machine's disk or stop stray work: `processes`, `runs` (finished
    runs' folders), `caches`, `docker`. Says how much space came back. Logins
    and services are never touched; only a person can reset a machine's home.
    """
    return await machine_tools.reset_machine_internal(ctx.deps, request)


_WORKSPACE_CLI_BASE_TOOLS = [
    exec_command,
    manage_process,
    execute_python,
]

workspace_cli_toolset = FunctionToolset[BaseAgentContext](
    tools=list(_WORKSPACE_CLI_BASE_TOOLS)
)

# The machine tools ride with WORKSPACE_CLI -- an agent that runs commands can
# name its machines, open a preview, copy files to the pod and clean up -- but
# as a toolset of their own, so the pod's assistant can keep them behind tool
# search: they are four schemas most chats never need in their prefix.
machine_toolset = FunctionToolset[BaseAgentContext](
    tools=[list_machines, expose_port, sync_files, reset_machine]
)

# `view_image` lives in its own always-on-when-supported toolset (see
# `registry.py`'s VIEW_IMAGE toolset) rather than workspace_cli, so it reaches
# any agent with a vision-capable model regardless of configured toolsets.
view_image_toolset = FunctionToolset[BaseAgentContext](tools=[view_image])


def is_workspace_cli_toolset(toolset: object) -> bool:
    """True for the workspace CLI toolset (the capability assembler keys its
    usage-prompt special handling off this identity check)."""
    return toolset is workspace_cli_toolset
