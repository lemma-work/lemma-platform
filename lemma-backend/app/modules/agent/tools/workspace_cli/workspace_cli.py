from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.image_payload import downscale_for_vision
from app.modules.agent.tools.vision_delegation import describe_single_image
from app.modules.agent.tools.file_access import (
    read_pod_file_bytes,
    read_workspace_file_bytes,
)
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.agent.tools.tool_errors import (
    approval_error_result,
    safe_described_error,
    safe_error_text,
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
    ViewImageRequest,
    ViewImageResponse,
    WriteStdinRequest,
)
from app.modules.agent.tools.workspace_cli.github_credential_bridge import (
    looks_like_git_command,
)
from app.modules.agent.tools.workspace_cli.github_project import (
    prepare_project_directory,
)
from app.modules.agent.tools.workspace_cli.helper import (
    render_terminal_result,
    trim_python_result,
)
from app.modules.agent.tools.workspace_entities import PythonExecutionResult
from app.modules.workspace.session_support import retry_advice
from app.composition.agent_workspace import (
    get_workspace_tool_runtime,
)
from pydantic_ai import ToolReturn, BinaryContent
import mimetypes

logger = get_logger(__name__)
_DEFAULT_EXEC_YIELD_TIME_MS = 30000
_DEFAULT_EXEC_TIMEOUT_S = 60
# Conservative per-image ceiling: Anthropic caps an image source at ~5 MB, and
# other providers are similar. Over this, ask the agent to downscale first rather
# than letting the provider reject the request mid-run.
MAX_VIEW_IMAGE_BYTES = 5 * 1024 * 1024


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
        exc_info=True,
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
        exc_info=True,
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
):
    runtime_context = workspace_runtime_context(ctx)
    runtime = get_workspace_tool_runtime()
    return await runtime.get_session(
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


async def exec_command_internal(
    ctx: BaseAgentContext,
    request: ExecCommandRequest,
) -> ExecCommandResult:
    try:
        runtime = get_workspace_tool_runtime()
        runtime_context = workspace_runtime_context(ctx)

        with run_phase("tool.workspace.session"):
            workspace_session = await get_workspace_session(
                ctx,
                session_id=runtime_context.default_shell_session_id,
                close_on_exit=False,
            )
        async with workspace_session:
            # A repo-backed conversation needs credentials for every command,
            # not just git-looking ones: the clone that puts the project on disk
            # has to happen before whatever the agent actually asked for, even
            # when that is `ls`.
            project_notice = await prepare_project_directory(
                ctx,
                workspace_session,
                wanted=ctx.workspace_repo is not None
                or looks_like_git_command(request.cmd),
            )
            if request.tty:
                effective_yield_time_ms = request.yield_time_ms
                effective_timeout = _DEFAULT_EXEC_TIMEOUT_S
            elif request.timeout_seconds is not None:
                # Explicit blocking: no yield window, wait until done
                effective_yield_time_ms = None
                effective_timeout = request.timeout_seconds
            else:
                effective_yield_time_ms = (
                    request.yield_time_ms
                    if request.yield_time_ms is not None
                    else _DEFAULT_EXEC_YIELD_TIME_MS
                )
                effective_timeout = _DEFAULT_EXEC_TIMEOUT_S
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
        stdout, stderr = render_terminal_result(result, tty=request.tty)
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
        stdout, stderr = render_terminal_result(result, tty=True)
        return ExecCommandResult(
            success=bool(result.get("success")),
            stdout=stdout,
            stderr=stderr,
            exit_code=result.get("exit_code"),
            completed=completed,
            process_id=result.get("process_id"),
            error=result.get("error"),
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
        # One sandbox serves every conversation belonging to a user, so this
        # list spans all of them. Show only what this conversation may drive:
        # its own processes, plus any that no conversation currently owns.
        # Rebinding indiscriminately would let a parent agent take over the
        # processes its own sub-agents started, since a sub-agent shares the
        # sandbox but has its own session.
        session_id = workspace_session.session_id
        visible: list[dict[str, Any]] = []
        for process in processes:
            process_id = str(process["process_id"])
            owner = await runtime.resolve_session_for_process(process_id)
            if owner is None:
                # Unowned: its binding expired, or it was started outside the
                # tool path. Claiming it here is how an agent recovers a
                # process it can otherwise no longer address.
                if not process.get("completed") and session_id:
                    await runtime.bind_process_to_session(
                        process_id=process_id,
                        session_id=session_id,
                    )
                visible.append(process)
            elif owner == session_id:
                visible.append(process)
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
                ctx, workspace_session, wanted=ctx.workspace_repo is not None
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


async def view_image_internal(
    ctx: BaseAgentContext,
    request: ViewImageRequest,
):
    # Require exactly one store path, returning a structured error (never raising)
    # so a wrong call surfaces success=False to the model instead of aborting the
    # run or burning the retry budget. Pick the store the agent explicitly
    # addressed — no path-shape inference.
    pod_path = (request.pod_file_path or "").strip()
    workspace_path = (request.workspace_file_path or "").strip()
    if bool(pod_path) == bool(workspace_path):
        return ViewImageResponse(
            success=False,
            error=(
                "Provide exactly one of `pod_file_path` (datastore) or "
                "`workspace_file_path` (sandbox)."
            ),
        )
    if pod_path:
        file_path = pod_path
        source = "datastore"
    else:
        file_path = workspace_path
        source = "workspace"

    try:
        if source == "datastore":
            content, detected_mime = await read_pod_file_bytes(ctx, file_path)
        else:
            content, detected_mime = await read_workspace_file_bytes(ctx, file_path)
    except DomainError as exc:
        # Datastore reads are grant-checked; surface a missing grant as
        # needs_approval so the agent can request access, like the pod tools.
        return approval_error_result(
            exc, tool_name="view_image", args=request.model_dump()
        )
    except Exception as exc:
        return ExecCommandResult(success=False, error=safe_error_text(exc))

    media_type = detected_mime or mimetypes.guess_type(file_path)[0]
    if not media_type or not media_type.startswith("image/"):
        if media_type == "application/pdf" or file_path.lower().endswith(".pdf"):
            hint = (
                "This is a PDF, not an image. Use `pod_view_document_pages` to see "
                "pages (layout, tables, figures), or `pod_read_file` to read "
                "the text."
            )
        else:
            hint = (
                f"This file is not an image (detected type: {media_type or 'unknown'}). "
                "`view_image` only handles image files. For documents, use "
                "`pod_read_file`; for PDFs, `pod_view_document_pages`."
            )
        return ViewImageResponse(
            success=False,
            error=hint,
            file_path=file_path,
            media_type=media_type,
            source=source,
        )

    # Sized for the model before it is measured against the limit. A phone
    # photo is several megabytes of pixels the model shrinks on arrival and
    # never looks at — so refusing it and telling the agent to go and compress
    # it was work nobody needed to do, on an image we were about to shrink
    # ourselves. What is left after this is what a limit should be judging.
    payload, payload_media_type = downscale_for_vision(content, media_type)
    if len(payload) > MAX_VIEW_IMAGE_BYTES:
        return ViewImageResponse(
            success=False,
            error=(
                f"Image is {len(payload) // 1024} KB even after downscaling, "
                f"over the {MAX_VIEW_IMAGE_BYTES // (1024 * 1024)} MB limit. "
                "Crop it or split it up before viewing."
            ),
            file_path=file_path,
            media_type=media_type,
            source=source,
            size_bytes=len(content),
        )

    # Only a model that can actually accept image parts is given them. Handing
    # BinaryContent to a text-only model poisons the whole request, and the
    # provider rejects the turn rather than the tool call.
    if getattr(ctx, "vision_mode", AgentVisionMode.UNAVAILABLE) is not (
        AgentVisionMode.DIRECT
    ):
        # The delegate is a vision model too, and pays the same way for pixels
        # past its own ceiling.
        return await describe_single_image(
            ctx,
            data=payload,
            media_type=payload_media_type,
            file_path=file_path,
            source=source,
            instructions=request.instructions,
        )

    return ToolReturn(
        return_value=ViewImageResponse(
            success=True,
            message=f"Successfully read image {file_path}",
            file_path=file_path,
            media_type=media_type,
            source=source,
            size_bytes=len(content),
        ),
        content=[
            BinaryContent(data=payload, media_type=payload_media_type),
        ],
    )
