from __future__ import annotations

from typing import Literal, Optional, List

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse


WORKSPACE_TOOL_COMMENT_DESC = "One-line statement of intent, shown to the user."


class ExecCommandRequest(BaseModel):
    cmd: str = Field(description="Shell command to run, exactly as in a terminal.")
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )
    max_output_tokens: int = Field(
        default=10000,
        description="Truncate stdout/stderr past this many tokens.",
    )
    tty: bool = Field(
        default=False,
        description=(
            "Allocate an interactive terminal. Required for commands that stay "
            "alive or wait for input (`npm run dev`, `python`). Such a process "
            "does not survive an idle pause."
        ),
    )
    cols: int = Field(
        default=120,
        ge=20,
        le=500,
        description="Terminal width for `tty` commands; widen for wide output.",
    )
    rows: int = Field(
        default=40,
        ge=5,
        le=200,
        description="Terminal height for `tty` commands.",
    )
    workdir: Optional[str] = Field(
        default=None,
        description="Working directory; relative paths resolve inside the workspace.",
    )
    yield_time_ms: Optional[int] = Field(
        default=None,
        description="Milliseconds to wait for output before returning.",
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=10,
        le=300,
        description=(
            "Block up to this many seconds instead of the default 30s yield "
            "window; always returns `completed: true` with no `process_id`."
        ),
    )


class WriteStdinRequest(BaseModel):
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )
    process_id: str = Field(
        description=(
            "Interactive process ID returned by `exec_command` when the command "
            "has not completed yet."
        )
    )
    chars: Optional[str] = Field(
        default=None,
        description=(
            'Characters to send to stdin. Use `""` to poll output without sending '
            "input. Include `\\n` when pressing Enter is required. Control keys "
            "are sent as their characters: `\\u0003` interrupts (Ctrl-C), "
            "`\\u0004` sends EOF (Ctrl-D) to exit a REPL, and `\\u001b[A` / "
            "`\\u001b[B` are the up and down arrows. Prefer interrupting a stuck "
            "process over abandoning it."
        ),
    )
    max_output_tokens: int = Field(
        default=10000,
        description=(
            "Maximum output tokens to return for this stdin write or poll call. "
            "Defaults to 10000."
        ),
    )
    yield_time_ms: Optional[int] = Field(
        default=None,
        description=(
            "How long to wait for new output after writing stdin, in milliseconds."
        ),
    )


class TerminateProcessRequest(BaseModel):
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )
    process_id: str = Field(
        description="Process ID returned by `exec_command` for the process to stop."
    )


class ListProcessesRequest(BaseModel):
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )


class ManageProcessRequest(BaseModel):
    """Drive a process started by `exec_command` (interactive or long-running)."""

    action: Literal["input", "kill", "list", "resize"] = Field(
        description="What to do with the process."
    )
    process_id: Optional[str] = Field(
        default=None,
        description="From `exec_command`. Required for every action but 'list'.",
    )
    chars: Optional[str] = Field(
        default=None,
        description=(
            'Stdin for action="input"; `""` polls output without sending. Include '
            "`\\n` for Enter. `\\u0003` is Ctrl-C, `\\u0004` is Ctrl-D."
        ),
    )
    cols: int = Field(default=120, ge=20, le=500, description="Width, for 'resize'.")
    rows: int = Field(default=40, ge=5, le=200, description="Height, for 'resize'.")
    max_output_tokens: int = Field(
        default=10000,
        description="Truncate returned output past this many tokens.",
    )
    yield_time_ms: Optional[int] = Field(
        default=None,
        description="Milliseconds to wait for new output.",
    )
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )


class ExecutePythonRequest(BaseModel):
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )
    code: str = Field(
        description="Python code to execute in the shared task kernel. The final expression value is returned separately when available."
    )
    timeout_seconds: int = Field(
        default=60,
        description="Maximum execution time in seconds before timing out.",
    )


class ViewImageRequest(BaseModel):
    pod_file_path: Optional[str] = Field(
        default=None,
        description=(
            "Image in the pod datastore, e.g. `/me/photo.jpg`. Set this or "
            "`workspace_file_path`, not both."
        ),
    )
    workspace_file_path: Optional[str] = Field(
        default=None,
        description=(
            "Image in the workspace sandbox, e.g. `images/output.png` — for "
            "artifacts you just produced. Set this or `pod_file_path`, not both."
        ),
    )
    # NB: the "exactly one path" rule is enforced in view_image_internal, not via a
    # model_validator. A raising validator is an argument-validation error that
    # bypasses the graceful tool-error boundary, burns the agent's retry budget,
    # and can abort the run — so we return a structured success=False instead.


class ExecCommandResult(BaseToolResponse):
    stdout: Optional[str] = Field(
        default=None,
        description="Captured standard output from the command or interactive session.",
    )
    stderr: Optional[str] = Field(
        default=None,
        description="Captured standard error from the command or interactive session.",
    )
    exit_code: Optional[int] = Field(
        default=None,
        description="Process exit code when the command has completed.",
    )
    completed: bool = Field(
        default=True,
        description=(
            "Whether the process has finished. Interactive TTY sessions usually "
            "return `false` until exited."
        ),
    )
    process_id: Optional[str] = Field(
        default=None,
        description=(
            "Process ID to reuse with `write_stdin` for follow-up interaction "
            "when this interactive process is still running."
        ),
    )


class ResizeTerminalRequest(BaseModel):
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )
    process_id: str = Field(
        description="Interactive process ID returned by `exec_command`."
    )
    cols: int = Field(
        ge=20,
        le=500,
        description="New terminal width in columns.",
    )
    rows: int = Field(
        ge=5,
        le=200,
        description="New terminal height in rows.",
    )


class ProcessInfo(BaseModel):
    process_id: str
    # What the agent needs to act: which process, and whether it is still
    # going. Everything below is descriptive.
    completed: bool = False
    exit_code: Optional[int] = None
    # Defaulted because the sandbox runtime is the only thing that still knows
    # what is running, and it does not report the command line or the working
    # directory a process was started in -- those used to be held by a control
    # plane that no longer exists. Reporting them as blank is honest; inventing
    # them would tell an agent a process is somewhere it is not.
    cmd: str = ""
    cwd: str = ""
    tty: bool = False
    started_at: float = 0.0


class ListProcessesResult(BaseToolResponse):
    processes: List[ProcessInfo] = Field(
        default_factory=list,
        description="Tracked shell processes in the conversation workspace.",
    )


class ViewImageResponse(BaseToolResponse):
    file_path: Optional[str] = Field(
        default=None,
        description="Resolved file path of the image that was loaded.",
    )
    media_type: Optional[str] = Field(
        default=None,
        description="Detected MIME type for the returned image content.",
    )
    source: Optional[str] = Field(
        default=None,
        description="Which store served the image: 'datastore' or 'workspace'.",
    )
    size_bytes: Optional[int] = Field(
        default=None,
        description="Size of the image in bytes.",
    )
