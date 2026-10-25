"""Where a conversation started, in the product-metrics vocabulary.

A view of its ``origin`` column, which says what the conversation is for --
and of the platform its surface binding names, the one thing about it still
kept in metadata until agent_surfaces' keys move to a table of their own.
"""

from __future__ import annotations

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import ConversationOrigin

#: The label each origin was counted under when it was the ``source`` key.
_SURFACE_LABELS: dict[ConversationOrigin, str] = {
    ConversationOrigin.WORKFLOW_STEP: "workflow",
    ConversationOrigin.SCHEDULE: "SCHEDULE",
    ConversationOrigin.POD_ASK: "pod_ask",
    ConversationOrigin.NOTIFICATION: "notification",
    ConversationOrigin.CHANNEL: "lemma_channel",
}


def surface_of(conversation: Conversation) -> str:
    """Where ``conversation`` started, as ``product_metrics`` counts it.

    A person in the app (or through the API, which writes the same rows and is
    not told apart here) is ``app``. An external surface names its platform in
    ``surface_platform``; the label is folded onto a fixed set by
    ``product_metrics.surface_label``.
    """
    stored = conversation.metadata or {}
    platform = stored.get("surface_platform")
    if conversation.origin is ConversationOrigin.SURFACE:
        return platform if isinstance(platform, str) and platform else "other"
    if conversation.origin is ConversationOrigin.CONTACT:
        if stored.get("web_widget_id"):
            return "web_widget"
        return platform if isinstance(platform, str) and platform else "other"
    return _SURFACE_LABELS.get(conversation.origin, "app")
