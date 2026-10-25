"""The chats list asks for the conversations of no channel: a channel's are
reached through the channel -- the member's own, a group's, a contact's."""

from __future__ import annotations

from uuid import uuid4

import app.modules.pod.infrastructure.models  # noqa: F401  (mappers resolve "Pod")
from app.modules.agent.domain.value_objects import ConversationAgentSelection
from app.modules.agent.infrastructure.db.conversation_list_page import (
    list_statement,
)


def _sql(in_channel: bool | None) -> str:
    selection = ConversationAgentSelection.all()
    return str(
        list_statement(
            user_id=uuid4(),
            pod_id=uuid4(),
            agent_selection=selection,
            status=None,
            conversation_type=None,
            metadata_filters=None,
            parent_id=None,
            archived=False,
            search=None,
            in_channel=in_channel,
        )
    )


def test_the_chats_list_keeps_the_conversations_of_no_channel() -> None:
    assert "agent_conversations.channel_id IS NULL" in _sql(False)
    assert "agent_conversations.channel_id IS NOT NULL" in _sql(True)
    assert "channel_id IS" not in _sql(None)
