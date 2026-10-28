"""Which surface a new conversation is counted under."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.conversation_metadata import surface_of
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import ConversationOrigin

_O = ConversationOrigin


@pytest.mark.parametrize(
    "origin, started_on, metadata, surface",
    [
        (_O.CHAT, None, None, "app"),
        (_O.CHAT, None, {}, "app"),
        (_O.SUBAGENT, None, {}, "app"),
        (_O.CONTACT, "web_widget", {}, "web_widget"),
        (_O.SURFACE, "SLACK", {}, "SLACK"),
        (_O.SURFACE, None, {}, "other"),
        (_O.CONTACT, "WHATSAPP", {}, "WHATSAPP"),
        (_O.NOTIFICATION, "SLACK", {}, "notification"),
        (_O.WORKFLOW_STEP, None, {}, "workflow"),
        (_O.SCHEDULE, None, {}, "SCHEDULE"),
        (_O.CHANNEL, None, {}, "lemma_channel"),
        # A tag a client wrote counts for nothing.
        (_O.CHAT, None, {"source": "notification", "surface_platform": "x"}, "app"),
        (_O.SURFACE, None, {"surface_platform": "SLACK"}, "other"),
    ],
)
def test_surface_of(origin, started_on, metadata, surface) -> None:
    conversation = Conversation(
        user_id=uuid4(), pod_id=uuid4(), origin=origin, metadata=metadata
    )
    assert surface_of(conversation, started_on) == surface
