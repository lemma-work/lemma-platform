"""What an agent's tools need to drive a workspace.

Separate from `contracts/execution.py`, which is value objects and imports
nothing: these reach this module's services, and a contract that pulls the
service layer in must not sit where every consumer of a DTO pays for it.

Replaces `app/composition/agent_workspace.py`. The shim published three service
classes to `agent` under a third module path, so `agent`'s build depended on
where inside `workspace` each class happened to live.
"""

from __future__ import annotations

from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from app.modules.workspace.services.workspace_tool_runtime import (
    get_workspace_tool_runtime,
    invalidate_function_workspace_env_cache,
)

__all__ = [
    "WorkspaceFileManager",
    "WorkspaceSandboxService",
    "get_workspace_tool_runtime",
    "invalidate_function_workspace_env_cache",
]
