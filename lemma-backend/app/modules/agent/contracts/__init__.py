"""Stable agent DTOs shared with delivery surfaces."""

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import AgentNotFoundError
from app.modules.agent.contracts.pod_summaries import (
    PodAgentSummary,
    list_agent_summaries_by_pod,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunApprovalDecision,
    MessageDraft,
    MessageKind,
    MessageRole,
    AgentToolset,
)
from app.modules.agent.api.schemas import AgentResponse
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent.tools.user_interaction.models import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)

__all__ = [
    # Same reason as `DatastoreTableNotFoundError` next door: the only error
    # that means "this agent is not here". `get_agent_by_name` authorizes after
    # the lookup, so catching everything reads a denial as absence.
    "AgentNotFoundError",
    "PodAgentSummary",
    "list_agent_summaries_by_pod",
    "AgentEvent",
    "AgentEventType",
    "AgentRunApprovalDecision",
    "AgentResponse",
    "AgentToolset",
    "AskUserRequest",
    "Conversation",
    "ConversationContext",
    "DisplayResourceRequest",
    "DisplayResourceType",
    "MessageDraft",
    "MessageKind",
    "MessageRole",
]
