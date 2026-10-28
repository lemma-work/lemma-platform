"""Where a conversation started, in the product-metrics vocabulary.

A view of its ``origin`` column, which says what the conversation is for, and
of what its opener says it started on: a platform or ``web_widget``. A surface
keeps that place in its own table, written after the row exists, so the
opener names it rather than this reading it back.
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

_STARTED_ON_A_SURFACE = frozenset(
    {ConversationOrigin.SURFACE, ConversationOrigin.CONTACT}
)


def surface_of(conversation: Conversation, started_on: str | None = None) -> str:
    """Where ``conversation`` started, as ``product_metrics`` counts it.

    A person in the app (or through the API, which writes the same rows and is
    not told apart here) is ``app``. A surface's conversation is counted under
    ``started_on``, which ``product_metrics.surface_label`` folds onto a fixed
    set.
    """
    if conversation.origin in _STARTED_ON_A_SURFACE:
        return started_on or "other"
    return _SURFACE_LABELS.get(conversation.origin, "app")
