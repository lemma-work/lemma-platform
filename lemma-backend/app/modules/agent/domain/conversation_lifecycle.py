"""Which conversation statuses count as still going, and which as finished.

A separate module from ``value_objects``: that file is at the architecture
ratchet's size limit, and these belong with each other more than with anything
already in it.

``WAITING`` is deliberately in neither set. A run that ends by asking a question
leaves its conversation waiting, and that is a resting state — neither active nor
finished. Treating it as active would tear down the pause ``request_approval``
and ``ask_user`` are built on; treating it as finished would report a turn as
over while somebody is still being asked.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRunStatus,
    ConversationStatus,
)

#: What a conversation shows while its latest run is in each status. Written
#: out rather than converted through the shared string value, so a run status
#: added without deciding what the conversation shows fails here, by name, and
#: not as a ``ValueError`` in whichever finalizer meets it first.
_CONVERSATION_STATUS_FOR_RUN: Mapping[AgentRunStatus, ConversationStatus] = (
    MappingProxyType(
        {
            AgentRunStatus.RUNNING: ConversationStatus.RUNNING,
            AgentRunStatus.STOP_REQUESTED: ConversationStatus.STOP_REQUESTED,
            AgentRunStatus.COMPLETED: ConversationStatus.COMPLETED,
            AgentRunStatus.FAILED: ConversationStatus.FAILED,
            AgentRunStatus.STOPPED: ConversationStatus.STOPPED,
        }
    )
)


def conversation_status_for(run_status: AgentRunStatus) -> ConversationStatus:
    """The conversation status a run in ``run_status`` leaves behind.

    Only the default: a run that ends on a pause leaves its conversation
    WAITING, and the finalizer that knows that says so explicitly.
    """
    return _CONVERSATION_STATUS_FOR_RUN[run_status]


#: Derived from the run sets so the two cannot drift apart.
ACTIVE_CONVERSATION_STATUSES = frozenset(
    conversation_status_for(status) for status in ACTIVE_AGENT_RUN_STATUSES
)

TERMINAL_CONVERSATION_STATUSES = frozenset(
    conversation_status_for(status) for status in TERMINAL_AGENT_RUN_STATUSES
)
