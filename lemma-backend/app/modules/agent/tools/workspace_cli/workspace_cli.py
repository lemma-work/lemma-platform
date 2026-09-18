from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.tools.tool_errors import (
    safe_described_error,
)
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecCommandResult,
    ExecutePythonRequest,
    ListProcessesRequest,
    ListProcessesResult,
    ProcessInfo,
    ResizeTerminalRequest,
    TerminateProcessRequest,
    WriteStdinRequest,
)
from app.modules.agent.tools.workspace_cli.github_credential_bridge import (
    looks_like_git_command,
    source_may_use_git,
)
from app.modules.agent.tools.workspace_cli.github_project import (
    prepare_project_directory,
)
from app.modules.agent.tools.workspace_cli.helper import (
    render_terminal_result,
    trim_python_result,
)
from app.modules.agent.tools.workspace_entities import PythonExecutionResult
from app.modules.agent.tools.workspace_cli.process_visibility import (
    visible_processes,
)
from app.modules.workspace.contracts.tooling import (
    retry_advice,
    get_workspace_tool_runtime,
)

logger = get_logger(__name__)
_DEFAULT_EXEC_YIELD_TIME_MS = 30000
_DEFAULT_EXEC_TIMEOUT_S = 60
# Conservative per-image ceiling: Anthropic caps an image source at ~5 MB, and
# other providers are similar. Over this, ask the agent to downscale first rather
# than letting the provider reject the request mid-run.


@dataclass(frozen=True)
class WorkspaceRuntimeContext:
    default_shell_session_id: str
    default_python_session_id: str
    initial_cwd: str
    scope_key: str


def workspace_runtime_context(ctx: BaseAgentContext) -> WorkspaceRuntimeContext:
    conversation_key = ctx.conversation_id.hex
    initial_cwd = ctx.get_workspace_cwd()
    cwd_key = uuid5(NAMESPACE_URL, initial_cwd).hex[:12]
    return WorkspaceRuntimeContext(
        default_shell_session_id=f"shell-{conversation_key}",
        # A stateful interpreter is created with a fixed cwd. Include the
        # conversation's resolved cwd in its identity so moving the conversation
        # cannot silently reuse a kernel rooted in the previous directory.
        default_python_session_id=f"python-{conversation_key}-{cwd_key}",
        initial_cwd=initial_cwd,
        scope_key=ctx.get_workspace_scope_key(),
    )


def _workspace_tool_failure(
    exc: Exception,
    *,
    operation: str,
    completed: bool = False,
    process_id: str | None = None,
) -> ExecCommandResult:
    logger.debug(
        "agent.workspace_cli.workspace_cli_s_s.diagnostic",
        operation=operation,
        exc_info=exc,
    )
    return ExecCommandResult(
        success=False,
        stdout="",
        stderr="",
        exit_code=None,
        completed=completed,
        process_id=process_id,
        error=(
            f"Workspace {operation} failed before the tool could complete: "
            f"{safe_described_error(exc)}." + retry_advice(exc)
        ),
    )


def _python_workspace_tool_failure(
    exc: Exception, *, operation: str
) -> PythonExecutionResult:
    logger.debug(
        "agent.workspace_cli.workspace_cli_s_s.diagnostic",
        operation=operation,
        exc_info=exc,
    )
    return PythonExecutionResult(
        success=False,
        stdout="",
        stderr="",
        result=None,
        error_in_exec={
            "ename": "WorkspaceToolError",
            "evalue": (
                f"Workspace {operation} failed before Python execution completed: "
                f"{safe_described_error(exc)}." + retry_advice(exc)
            ),
            "traceback": [],
        },
    )


async def get_workspace_session(
    ctx: BaseAgentContext,
    *,
    session_id: str | None,
    close_on_exit: bool,
    runtime=None,
):
    runtime_context = workspace_runtime_context(ctx)
    if runtime is None:
        runtime = get_workspace_tool_runtime()
    # Named here rather than left to the image, because a shell that inherits
    # the image's `AGENT_BROWSER_SESSION` is in the sandbox's *shared* browser.
    # The typed browser tools name their session per command; a raw
    # `agent-browser` in `exec_command` does not, and agents reach for one --
    # so the agent browsed in the default session while the person's pane
    # watched this conversation's, which showed a blank page throughout.
    from app.modules.workspace.contracts.browser import agent_session

    return await runtime.get_session(
        browser_session=agent_session(ctx.conversation_id),
        user_id=ctx.user_id,
        pod_id=ctx.pod_id,
        organization_id=ctx.organization_id,
        workload_type=ctx.workload_type,
        workload_id=ctx.workload_id,
        workload_name=ctx.agent_name,
        scope_key=runtime_context.scope_key,
        session_id=session_id,
        initial_cwd=runtime_context.initial_cwd,
        close_on_exit=close_on_exit,
    )


WORKSPACE_RECREATED_NOTICE = (
    "[workspace notice] This workspace was recreated since this conversation "
    "last used it, so files written earlier are gone. This is the one case "
    "where an empty working directory really does mean lost work — recreate "
    "anything you still need."
)


def _with_recreation_notice(text: str | None, *, recreated: bool) -> str | None:
    """Say once, explicitly, that files were lost — never make the agent guess."""

    if not recreated:
        return text
    return f"{WORKSPACE_RECREATED_NOTICE}\n{text or ''}"


def _with_notice(text: str | None, *, notice: str | None) -> str | None:
    """Prepend a one-off notice, on the same principle as the one above."""

    if not notice:
        return text
    return f"{notice}\n{text or ''}"


async def _process_control_tool(
    ctx: BaseAgentContext,
    *,
    process_id: str,
    operation: str,
    call,
    completed_default: bool,
) -> ExecCommandResult:
    """Run a control action against an already-running process.

    Terminate and resize differ only in the call they make and how they report
    completion, so they share one guard rather than repeating the failure
    shaping - and with it the judgement about what a half-finished control
    action means for the caller.
    """

    try:
        runtime = get_workspace_tool_runtime()
        runtime_context = workspace_runtime_context(ctx)
        resolved_session_id = (
            await runtime.resolve_session_for_process(process_id)
            or runtime_context.default_shell_session_id
        )
        workspace_session = await get_workspace_session(
            ctx,
            session_id=resolved_session_id,
            close_on_exit=False,
        )
        async with workspace_session:
            result = await call(workspace_session)
        if completed_default:
            await runtime.clear_process_binding(process_id)
        return ExecCommandResult(
            success=bool(result.get("success")),
            stdout=result.get("stdout"),
            stderr=result.get("stderr"),
            exit_code=result.get("exit_code"),
            completed=bool(result.get("completed", completed_default)),
            process_id=result.get("process_id") or process_id,
            error=result.get("error"),
        )
    except Exception as exc:
        return _workspace_tool_failure(
            exc,
            operation=operation,
            completed=False,
            process_id=process_id,
        )


async def resize_terminal_internal(
    ctx: BaseAgentContext,
    request: ResizeTerminalRequest,
) -> ExecCommandResult:
    return await _process_control_tool(
        ctx,
        process_id=request.process_id,
        operation="resize_terminal",
        call=lambda session: session.resize_terminal(
            process_id=request.process_id,
            cols=request.cols,
            rows=request.rows,
        ),
        completed_default=False,
    )


def exec_clocks(request: ExecCommandRequest) -> tuple[int, int | None]:
    """This call's patience, and when it may return early. They are independent.

    `sandbox_session.exec_command` keeps them apart all the way down --
    `wait_until` is how long *this call* waits, and it is not the process's
    lifetime. The tool used to conflate them: `tty=true` overwrote an explicit
    `timeout_seconds` with the default, and an explicit `timeout_seconds` threw
    away an explicit `yield_time_ms`. Both silently, so a caller that set two
    parameters had one honoured and no way to learn which.
    """
    timeout = request.timeout_seconds or _DEFAULT_EXEC_TIMEOUT_S
    if request.yield_time_ms is not None:
        return timeout, request.yield_time_ms
    if request.timeout_seconds is not None:
        # A stated patience and no yield means "wait it out": returning early on
        # a quiet moment is the thing the caller opted out of.
        return timeout, None
    return timeout, _DEFAULT_EXEC_YIELD_TIME_MS


async def exec_command_internal(
    ctx: BaseAgentContext,
    request: ExecCommandRequest,
    *,
    runtime=None,
    prepare_project=None,
) -> ExecCommandResult:
    # The workspace runtime and the project-preparation step are arguments so a
    # test drives this function's own composition -- which session it opens,
    # and the `wanted=` decision below -- rather than replacing those names
    # inside this module and asserting against the replacement. Both default to
    # None and are resolved here rather than in the signature: a default
    # argument binds at import, which would make a double installed on either
    # module-level name unreachable without failing the test that installed it.
    try:
        if runtime is None:
            runtime = get_workspace_tool_runtime()
        if prepare_project is None:
            prepare_project = prepare_project_directory
        runtime_context = workspace_runtime_context(ctx)

        with run_phase("tool.workspace.session"):
            workspace_session = await get_workspace_session(
                ctx,
                session_id=runtime_context.default_shell_session_id,
                close_on_exit=False,
                runtime=runtime,
            )
        async with workspace_session:
            # A repo-backed conversation needs credentials for every command,
            # not just git-looking ones: the clone that puts the project on disk
            # has to happen before whatever the agent actually asked for, even
            # when that is `ls`.
            project_notice = await prepare_project(
                ctx,
                workspace_session,
                wanted=ctx.workspace_repo is not None
                or looks_like_git_command(request.cmd),
            )
            effective_timeout, effective_yield_time_ms = exec_clocks(request)
            with run_phase("tool.workspace.exec"):
                result = await workspace_session.exec_command(
                    cmd=request.cmd,
                    max_output_tokens=request.max_output_tokens,
                    tty=request.tty,
                    workdir=request.workdir,
                    yield_time_ms=effective_yield_time_ms,
                    timeout=effective_timeout,
                    cols=request.cols,
                    rows=request.rows,
                )
            completed = bool(result.get("completed", True))
            process_id = result.get("process_id")
            if process_id and workspace_session.session_id and not completed:
                await runtime.bind_process_to_session(
                    process_id=process_id,
                    session_id=workspace_session.session_id,
                )
        stdout, stderr = render_terminal_result(
            result, tty=request.tty, max_output_tokens=request.max_output_tokens
        )
        stdout = _with_recreation_notice(
            stdout, recreated=workspace_session.workspace_recreated
        )
        stdout = _with_notice(stdout, notice=project_notice)
        return ExecCommandResult(
            success=bool(result.get("success")),
            stdout=stdout,
            stderr=stderr,
            exit_code=result.get("exit_code"),
            completed=completed,
            process_id=process_id if not completed else None,
            error=result.get("error"),
        )
    except Exception as exc:
        return _workspace_tool_failure(
            exc,
            operation="exec_command",
        )


async def write_stdin_internal(
    ctx: BaseAgentContext,
    request: WriteStdinRequest,
) -> ExecCommandResult:
    try:
        runtime = get_workspace_tool_runtime()
        runtime_context = workspace_runtime_context(ctx)
        resolved_session_id = (
            await runtime.resolve_session_for_process(request.process_id)
            or runtime_context.default_shell_session_id
        )
        workspace_session = await get_workspace_session(
            ctx,
            session_id=resolved_session_id,
            close_on_exit=False,
        )
        async with workspace_session:
            result = await workspace_session.write_stdin(
                process_id=request.process_id,
                chars=request.chars,
                max_output_tokens=request.max_output_tokens,
                yield_time_ms=request.yield_time_ms,
            )
        completed = bool(result.get("completed", True))
        if completed:
            await runtime.clear_process_binding(request.process_id)
        elif result.get("process_id") and workspace_session.session_id:
            await runtime.bind_process_to_session(
                process_id=str(result["process_id"]),
                session_id=workspace_session.session_id,
            )
        # write_stdin only ever targets an interactive process, so its output is
        # terminal output and is rendered as such.
        stdout, stderr = render_terminal_result(
            result, tty=True, max_output_tokens=request.max_output_tokens
        )
        return ExecCommandResult(
            success=bool(result.get("success")),
            stdout=stdout,
            stderr=stderr,
            exit_code=result.get("exit_code"),
            completed=completed,
            process_id=result.get("process_id"),
            error=result.get("error"),
            notice=result.get("notice"),
        )
    except Exception as exc:
        # Session setup failed before write_stdin established whether the
        # process is terminal. Preserve the routing binding so a later poll or
        # retry still reaches the original shell session.
        return _workspace_tool_failure(
            exc,
            operation="write_stdin",
            completed=False,
            process_id=request.process_id,
        )


async def terminate_process_internal(
    ctx: BaseAgentContext,
    request: TerminateProcessRequest,
) -> ExecCommandResult:
    return await _process_control_tool(
        ctx,
        process_id=request.process_id,
        operation="terminate_process",
        call=lambda session: session.terminate_process(request.process_id),
        completed_default=True,
    )


async def list_processes_internal(
    ctx: BaseAgentContext,
    request: ListProcessesRequest,
) -> ListProcessesResult:
    del request
    try:
        runtime = get_workspace_tool_runtime()
        runtime_context = workspace_runtime_context(ctx)
        workspace_session = await get_workspace_session(
            ctx,
            session_id=runtime_context.default_shell_session_id,
            close_on_exit=False,
        )
        async with workspace_session:
            processes = await workspace_session.list_processes()
        visible = await visible_processes(
            processes,
            runtime=runtime,
            session_id=workspace_session.session_id,
            own_cwd=runtime_context.initial_cwd,
        )
        return ListProcessesResult(
            success=True,
            processes=[ProcessInfo.model_validate(process) for process in visible],
        )
    except Exception as exc:
        logger.warning(
            "agent.workspace_cli.workspace_cli_list_processes_s.degraded",
            exc_info=True,
        )
        return ListProcessesResult(
            success=False,
            processes=[],
            error=(
                f"Workspace list_processes failed before the tool could complete: "
                f"{safe_described_error(exc)}. Treat this as a recoverable tool "
                "failure and retry if the operation is still needed."
            ),
        )


async def execute_python_internal(ctx: BaseAgentContext, request: ExecutePythonRequest):
    try:
        workspace_session = await get_workspace_session(
            ctx,
            session_id=workspace_runtime_context(ctx).default_python_session_id,
            close_on_exit=False,
        )
        async with workspace_session:
            project_notice = await prepare_project_directory(
                ctx,
                workspace_session,
                wanted=ctx.workspace_repo is not None
                or source_may_use_git(request.code),
            )
            result = await workspace_session.execute_code(
                request.code, request.timeout_seconds
            )
        trimmed = trim_python_result(result)
        if workspace_session.workspace_recreated:
            trimmed.stdout = _with_recreation_notice(trimmed.stdout, recreated=True)
        trimmed.stdout = _with_notice(trimmed.stdout, notice=project_notice)
        return trimmed
    except Exception as exc:
        return _python_workspace_tool_failure(exc, operation="execute_python")


# Re-exported: `view_image` lives in its own module for size, and every caller
# — the adapter, the tests — reaches it through this one.
from app.modules.agent.tools.workspace_cli.view_image import (  # noqa: E402
    MAX_VIEW_IMAGE_BYTES as MAX_VIEW_IMAGE_BYTES,
    view_image_internal as view_image_internal,
)
