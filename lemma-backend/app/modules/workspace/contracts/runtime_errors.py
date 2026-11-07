"""What a machine operation can fail with, for a caller that reports it."""

from __future__ import annotations


def machine_runtime_errors() -> tuple[type[BaseException], ...]:
    from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
    from sandbox_runtime.errors import SandboxError

    return (SandboxError, WorkspaceRuntimeError)


__all__ = ["machine_runtime_errors"]
