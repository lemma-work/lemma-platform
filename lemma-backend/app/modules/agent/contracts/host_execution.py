"""The Agent Host link, as the workspace module needs it for host execution.

See docs/architecture/desktop-host-execution.md. The workspace's host provider
sends each sandbox operation as an ``op`` over the owner's link; selection asks
which of a user's hosts can take them. Both are published here rather than
reached into, so the link's internals stay the agent module's to change.
"""

from __future__ import annotations

from app.modules.agent.domain.agent_host_ops import (
    HOST_OFFLINE,
    HOST_OFFLINE_MESSAGE,
    AgentHostOpError,
)
from app.modules.agent.infrastructure.agent_host.host_execution import (
    host_execution_host_id,
)
from app.modules.agent.services.agent_host_ops import AgentHostOpClient


__all__ = [
    "HOST_OFFLINE",
    "HOST_OFFLINE_MESSAGE",
    "AgentHostOpClient",
    "AgentHostOpError",
    "host_execution_host_id",
]
