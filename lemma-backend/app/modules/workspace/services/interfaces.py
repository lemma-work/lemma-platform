"""Abstract interfaces for workspace sandbox operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Any
from uuid import UUID

from app.modules.workspace.contracts import SandboxInfo, PythonExecutionResult, ShellCommandResult


class ISandbox(ABC):
    """Interface for managing a user-scoped code sandbox."""

    @abstractmethod
    async def ensure_sandbox(
        self,
        user_id: UUID,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxInfo:
        """Create or resume a sandbox for a user."""
        pass

    @abstractmethod
    async def get_sandbox(self, user_id: UUID) -> Optional[SandboxInfo]:
        """Get sandbox information by user ID."""
        pass

    @abstractmethod
    async def delete_sandbox(self, user_id: UUID) -> None:
        """Delete a user's sandbox."""
        pass

    async def suspend_sandbox(self, user_id: UUID) -> None:
        """Release compute while retaining the user's logical sandbox identity.

        Providers without a suspend primitive retain historical behavior by
        deleting compute; the next ensure still uses the same logical user ID.
        """

        await self.delete_sandbox(user_id)

    @abstractmethod
    async def is_sandbox_running(self, user_id: UUID) -> bool:
        """Check if the user's sandbox is running."""
        pass

    async def heartbeat(self, user_id: UUID) -> None:
        """Reset the sandbox's idle clock so the manager's reaper keeps it alive.

        Called when a sandbox is handed out for use so a reused-but-near-idle
        sandbox is not reaped out from under a sessionless workload (e.g. a
        function run, which holds no runtime session). Default no-op for
        providers without an idle reaper.
        """
        return None


class IWorkspaceSession(ABC):
    """
    Pure workspace session interface for operations.

    This interface is independent of the sandbox provider. Implementations may
    talk to AgentBox directly, to a local Docker runtime, or to another adapter.
    """

    @abstractmethod
    async def execute_code(
        self,
        code: str,
        timeout: int = 30,
    ) -> PythonExecutionResult:
        """
        Execute Python code in the session.
        """
        pass

    @abstractmethod
    async def execute_terminal_command(
        self,
        command: str,
        timeout: int = 30,
    ) -> ShellCommandResult:
        """Execute a terminal command in the session."""
        pass

    @abstractmethod
    async def exec_command(
        self,
        *,
        cmd: str,
        max_output_tokens: Optional[int] = None,
        tty: bool = False,
        workdir: Optional[str] = None,
        yield_time_ms: Optional[int] = None,
        timeout: Optional[int] = 300,
    ) -> dict[str, Any]:
        """Execute a shell command, optionally as a long-running process."""
        pass

    @abstractmethod
    async def write_stdin(
        self,
        *,
        process_id: str,
        chars: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        yield_time_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        """Write to, or poll, a long-running process."""
        pass

    @abstractmethod
    async def terminate_process(self, process_id: str) -> dict[str, Any]:
        """Terminate a long-running process."""
        pass

    @abstractmethod
    async def list_processes(self) -> list[dict[str, Any]]:
        """List tracked shell processes in the session."""
        pass

    @abstractmethod
    async def set_cwd(self, path: str) -> None:
        """Set the current working directory for session operations."""
        pass

    @abstractmethod
    async def get_cwd(self) -> str:
        """Get the current working directory."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the session and cleanup resources."""
        pass

    @abstractmethod
    async def __aenter__(self) -> "IWorkspaceSession":
        """Enter the async context manager."""
        pass

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> Optional[bool]:
        """Exit the async context manager."""
        pass
