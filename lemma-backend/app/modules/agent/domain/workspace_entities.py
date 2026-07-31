"""Workspace entity compatibility exports."""

from app.modules.workspace.contracts import (
    ContainerInfo,
    ExecutionResult,
    PythonExecutionResult,
    SandboxInfo,
    ShellCommandResult,
    WorkspaceStatus,
)

__all__ = [
    "ContainerInfo",
    "ExecutionResult",
    "PythonExecutionResult",
    "SandboxInfo",
    "ShellCommandResult",
    "WorkspaceStatus",
]
