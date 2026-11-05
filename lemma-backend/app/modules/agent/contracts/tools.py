"""What another module needs to hand the agent a tool: the ``ToolSpec`` type.

A chat platform's tools (``agent_surfaces``) are specs like the agent's own:
a plain async function taking a ``ToolContext`` and a request model, described
by ``tool``. The agent decides where they are served. A surface that renders
what the agent's own tools asked for -- a question, a resource to display --
reads their request models here too.
"""

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import (
    FlatArguments,
    ToolGroup,
    ToolPolicy,
    ToolSpec,
    tool,
)
from app.modules.agent.infrastructure.tools.user_interaction.models import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)

__all__ = [
    "AskUserRequest",
    "DisplayResourceRequest",
    "DisplayResourceType",
    "FlatArguments",
    "ToolContext",
    "ToolGroup",
    "ToolPolicy",
    "ToolSpec",
    "tool",
]
