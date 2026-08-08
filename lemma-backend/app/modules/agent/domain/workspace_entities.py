"""Workspace entity compatibility exports."""

from app.modules.workspace.contracts import (
    ContainerInfo,
    PythonExecutionResult,
    SandboxInfo,
    ShellCommandResult,
    WorkspaceStatus,
)

__all__ = [
    "ContainerInfo",
    "PythonExecutionResult",
    "SandboxInfo",
    "ShellCommandResult",
    "WorkspaceStatus",
]
