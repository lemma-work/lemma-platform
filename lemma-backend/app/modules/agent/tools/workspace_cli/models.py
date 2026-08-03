from __future__ import annotations

from typing import Literal, Optional, List

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse


WORKSPACE_TOOL_COMMENT_DESC = "Short, one-line goal statement for this tool call to show users what is being worked on."


class ExecCommandRequest(BaseModel):
    cmd: str = Field(
        description=(
            "Exact shell command to execute. Include the full command string as you "
            "would run it in a terminal. This runs inside an isolated workspace; "
            "`localhost` is the workspace container, not the host Lemma app. Use "
            "the injected Lemma CLI environment for pod operations."
        )
    )
    comment: Optional[str] = Field(
        default=None,
        description=WORKSPACE_TOOL_COMMENT_DESC,
    )
    max_output_tokens: int = Field(
        default=10000,
        description=(
            "Maximum output tokens to return before truncating stdout/stderr for "
            "this call. Defaults to 10000."
        ),
    )
    tty: bool = Field(
        default=False,
        description=(
            "Set true to allocate an interactive terminal session. Required for "
            "commands that stay alive or wait for input (for example `npm run dev`, "
            "`python`, `bash`). An interactive process does not survive the "
            "sandbox being paused while you are idle, so treat it as belonging "
            "to the current stretch of work."
        ),
    )
    cols: int = Field(
        default=120,
        ge=20,
        le=500,
        description=(
            "Terminal width for `tty` commands. Widen it for programs that "
            "render tables or wide output."
        ),
    )
    rows: int = Field(
        default=40,
        ge=5,
        le=200,
        description="Terminal height for `tty` commands.",
    )
    workdir: Optional[str] = Field(
        default=None,
        description=(
            "Working directory for the command. Relative paths are resolved inside "
            "the workspace."
        ),
    )
    yield_time_ms: Optional[int] = Field(
        default=None,
        description=(
            "How long to wait for output before returning, in milliseconds. Lower "
            "values stream progress faster; higher values batch more output."
        ),
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=10,
        le=300,
        description=(
            "Set a blocking timeout in seconds instead of the default 30-second yield window. "
            "When set, the command blocks until completion or the timeout expires — "
            "`completed: true` is always returned (no `process_id`). "
            "Use for commands expected to take longer than 30 s, e.g. large data fetches. "
            "Omit to use the default 30-second yield behavior."
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
        description=(
            "'input' = send chars to (or poll output from) a running process; "
            "'kill' = stop a process; 'list' = list tracked processes; "
            "'resize' = change an interactive terminal's size."
        )
    )
    process_id: Optional[str] = Field(
        default=None,
        description=(
            "Process ID from `exec_command`. Required for 'input', 'kill', and "
            "'resize'."
        ),
    )
    chars: Optional[str] = Field(
        default=None,
        description=(
            'For action="input": characters to send to stdin. Use `""` to poll '
            "output without sending input. Include `\\n` to press Enter. "
            "Control keys are sent as characters: `\\u0003` interrupts (Ctrl-C) "
            "and `\\u0004` sends EOF (Ctrl-D) to leave a REPL."
        ),
    )
    cols: int = Field(
        default=120,
        ge=20,
        le=500,
        description='For action="resize": new terminal width in columns.',
    )
    rows: int = Field(
        default=40,
        ge=5,
        le=200,
        description='For action="resize": new terminal height in rows.',
    )
    max_output_tokens: int = Field(
        default=10000,
        description='For action="input": max output tokens to return. Defaults to 10000.',
    )
    yield_time_ms: Optional[int] = Field(
        default=None,
        description='For action="input": how long to wait for new output, in ms.',
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
            "Path to an image file in the pod datastore, e.g. `/me/photo.jpg`. "
            "These are user-uploaded or ingested files; use `pod_list_files` or "
            "`pod_search_files` to discover exact paths. Set this OR "
            "`workspace_file_path`, not both."
        ),
    )
    workspace_file_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to an image file in the conversation workspace sandbox — a "
            "relative path such as `images/output.png` or one under `/workspace/`. "
            "Use for artifacts the agent just produced (screenshots, charts). Set "
            "this OR `pod_file_path`, not both."
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
    cmd: str
    cwd: str
    tty: bool = False
    started_at: float
    completed: bool = False
    exit_code: Optional[int] = None


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
