"""Host execution, as the agent module drives it.

See docs/architecture/desktop-host-execution.md. The agent module decides that
a run executes on the user's Mac (§2); this is how it then opens the run's
host sandbox and learns its root. Tool sessions on it come from
``get_workspace_tool_runtime().get_host_session``.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.workspace.domain.host_execution import (
    HostFolder,
    HostWorkspace,
    host_sandbox_id,
    is_host_sandbox_id,
)


async def open_host_workspace(
    *,
    owner_id: UUID,
    conversation_id: UUID,
    host_id: UUID,
    day: str,
    slug: str,
    root_hint: str | None,
) -> HostWorkspace:
    """See ``services/host_workspace.open_host_workspace``.

    Imported where it is called: the tool context imports this contract for
    ``HostWorkspace`` on every start, and the provider stack behind opening a
    workspace is only needed on the one install that runs commands on a host.
    """
    from app.modules.workspace.services import host_workspace

    return await host_workspace.open_host_workspace(
        owner_id=owner_id,
        conversation_id=conversation_id,
        host_id=host_id,
        day=day,
        slug=slug,
        root_hint=root_hint,
    )


__all__ = [
    "HostFolder",
    "HostWorkspace",
    "host_sandbox_id",
    "is_host_sandbox_id",
    "open_host_workspace",
]
