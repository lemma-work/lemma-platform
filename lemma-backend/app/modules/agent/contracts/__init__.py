"""Stable agent DTOs shared with delivery surfaces."""

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import AgentNotFoundError, RuntimeUnavailableError
from app.modules.agent.contracts.pod_summaries import (
    PodAgentSummary,
    list_agent_summaries_by_pod,
)
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunApprovalDecision,
    MessageDraft,
    MessageKind,
    MessageRole,
    AgentToolset,
)
from app.modules.agent.contracts.agent_response import AgentResponse
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.user_interaction.models import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)

__all__ = [
    # Same reason as `DatastoreTableNotFoundError` next door: the only error
    # that means "this agent is not here". `get_agent_by_name` authorizes after
    # the lookup, so catching everything reads a denial as absence.
    "AgentNotFoundError",
    # A turn refused before its run exists because the teammate's model cannot
    # run for this person -- most often another member's own runtime. A surface
    # says why instead of retrying a refusal that will not change.
    "RuntimeUnavailableError",
    "PodAgentSummary",
    "list_agent_summaries_by_pod",
    "AgentEvent",
    "AgentEventType",
    "AgentRunApprovalDecision",
    "AgentKind",
    "AgentResponse",
    "AgentToolset",
    "AskUserRequest",
    "Conversation",
    "ToolContext",
    "DisplayResourceRequest",
    "DisplayResourceType",
    "MessageDraft",
    "MessageKind",
    "MessageRole",
]
