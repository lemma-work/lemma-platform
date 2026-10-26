"""What another module needs to hand the agent a tool: the ``ToolSpec`` type.

A chat platform's tools (``agent_surfaces``) are specs like the agent's own:
a plain async function taking a ``ToolContext`` and a request model, described
by ``tool``. The agent decides where they are served.
"""

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import (
    FlatArguments,
    ToolGroup,
    ToolPolicy,
    ToolSpec,
    tool,
)

__all__ = [
    "FlatArguments",
    "ToolContext",
    "ToolGroup",
    "ToolPolicy",
    "ToolSpec",
    "tool",
]
