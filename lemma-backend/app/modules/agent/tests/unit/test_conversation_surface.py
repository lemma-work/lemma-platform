"""Which surface a new conversation is counted under."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.conversation_metadata import surface_of
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import ConversationOrigin

_O = ConversationOrigin


@pytest.mark.parametrize(
    "origin, metadata, surface",
    [
        (_O.CHAT, None, "app"),
        (_O.CHAT, {}, "app"),
        (_O.SUBAGENT, {}, "app"),
        (_O.CONTACT, {"web_widget_id": str(uuid4())}, "web_widget"),
        (_O.SURFACE, {"surface_platform": "SLACK"}, "SLACK"),
        (_O.SURFACE, {}, "other"),
        (_O.CONTACT, {"surface_platform": "WHATSAPP"}, "WHATSAPP"),
        (_O.NOTIFICATION, {"surface_platform": "SLACK"}, "notification"),
        (_O.WORKFLOW_STEP, {}, "workflow"),
        (_O.SCHEDULE, {}, "SCHEDULE"),
        (_O.CHANNEL, {}, "lemma_channel"),
        # A tag a client wrote counts for nothing.
        (_O.CHAT, {"source": "notification", "workflow_run_id": "x"}, "app"),
    ],
)
def test_surface_of(origin, metadata, surface) -> None:
    conversation = Conversation(
        user_id=uuid4(), pod_id=uuid4(), origin=origin, metadata=metadata
    )
    assert surface_of(conversation) == surface
