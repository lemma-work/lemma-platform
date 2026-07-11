"""Stable workspace execution and sandbox value objects."""

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel


@dataclass
class SandboxInfo:
    sandbox_id: str
    namespace: str
    status: str
    image: str
    created_at: str | None = None
    endpoint: str | None = None
    name: str | None = None

    @property
    def container_name(self) -> str:
        return self.name or self.sandbox_id

    @property
    def pod_name(self) -> str:
        return self.name or self.sandbox_id


ContainerInfo = SandboxInfo


class WorkspaceStatus(str, Enum):
    CREATING = "CREATING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


@dataclass
class ExecutionResult:
    success: bool
    output: str
    error: str | None = None
    execution_count: int | None = None
    data: dict[str, Any] | None = None


class ShellCommandResult(BaseModel):
    success: bool
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | dict[str, Any] | None = None
    current_working_directory: str | None = None

    def __str__(self) -> str:
        return (
            "# ShellCommandResult\n"
            f"success={self.success}\nexit_code={self.exit_code}\n"
            f"stdout={self.stdout}\nstderr={self.stderr}\nerror={self.error}\n"
            f"cwd={self.current_working_directory}"
        )

    @property
    def full_error_message(self) -> str | None:
        if self.success:
            return None
        parts: list[str] = []
        if self.stdout and self.stdout.strip():
            parts.append(f"Stdout: {self.stdout.strip()}")
        if self.stderr and self.stderr.strip():
            parts.append(f"Stderr: {self.stderr.strip()}")
        if isinstance(self.error, str) and self.error.strip():
            parts.append(f"Error: {self.error.strip()}")
        elif isinstance(self.error, dict):
            parts.append(f"Error: {self.error}")
        return "\n".join(parts) or (
            f"Command failed with exit code {self.exit_code} but no output."
        )


class PythonExecutionResult(BaseModel):
    success: bool
    stdout: str | None = None
    stderr: str | None = None
    result: str | None = None
    error_in_exec: dict[str, Any] | None = None
    execution_count: int | None = None
    data: dict[str, Any] | None = None

    @property
    def error(self) -> str | None:
        if not self.error_in_exec:
            return None
        name = self.error_in_exec.get("ename", "")
        value = self.error_in_exec.get("evalue", "")
        traceback = self.error_in_exec.get("traceback", [])
        if traceback:
            return f"{name}: {value}\n" + "\n".join(traceback)
        return f"{name}: {value}"
