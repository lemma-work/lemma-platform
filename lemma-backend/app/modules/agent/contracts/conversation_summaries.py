"""What other modules may know about one person's conversations across many pods.

An organization's overview wants, for every pod a member can open, what is
waiting on them, their latest chats and how busy each pod has been. Asked per
pod that is a request per pod per question; each read here is one statement
for the whole set.

**The pods are the caller's to have authorized.** Membership is the
precondition of conversation access (``CONVERSATION_ENUMERATION``), so a caller
passes only pods it has already found the person may open -- the identity
module passes ``pod.contracts.list_visible_pods``. Inside those pods every read
is of the person's own conversations, the same rows the per-pod lists return,
and never another member's.
"""

from __future__ import annotations

from app.modules.agent.domain.conversation_origin import (
    CHATS,
    STARTED_BY_AN_AGENT,
    ConversationOrigin,
)
from app.modules.agent.domain.pending_interactions import (
    PendingInteraction,
    PendingInteractionKind,
    PodPendingInteraction,
)
from app.modules.agent.domain.recent_conversations import DayActivity, RecentChat
from app.modules.agent.infrastructure.repositories.pending_interaction_queries import (
    list_pending_interactions_in_pods,
)
from app.modules.agent.infrastructure.repositories.recent_conversation_queries import (
    daily_activity_by_pod,
    recent_chats_by_pod,
)

__all__ = [
    "CHATS",
    "ConversationOrigin",
    "DayActivity",
    "PendingInteraction",
    "PendingInteractionKind",
    "PodPendingInteraction",
    "RecentChat",
    "STARTED_BY_AN_AGENT",
    "daily_activity_by_pod",
    "list_pending_interactions_in_pods",
    "recent_chats_by_pod",
]
