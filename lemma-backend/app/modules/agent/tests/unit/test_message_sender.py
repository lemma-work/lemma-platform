"""Which person a message came from, carried from the draft to the row.

`role` says a human spoke. These pin that the *which one* survives the trip,
and that nothing else acquires an author on the way.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    MessageDraft,
    MessageKind,
    MessageRole,
)


def test_a_user_draft_carries_its_sender_into_the_message():
    sender_id = uuid4()
    draft = MessageDraft.of_text(
        "hello", role=MessageRole.USER, sender_user_id=sender_id
    )
    message = Message.from_draft(
        draft, conversation_id=uuid4(), sequence=0, agent_run_id=uuid4()
    )
    assert message.sender_user_id == sender_id


def test_an_assistant_draft_has_no_sender():
    """Nothing derives a sender from `role`, so an agent's own text stays
    unattributed rather than inheriting whoever prompted it."""
    draft = MessageDraft.of_text("the answer")
    message = Message.from_draft(
        draft, conversation_id=uuid4(), sequence=1, agent_run_id=uuid4()
    )
    assert message.role is MessageRole.ASSISTANT
    assert message.sender_user_id is None


def test_tool_drafts_have_no_sender():
    draft = MessageDraft.of_tool_call(
        tool_name="read_file", tool_call_id="tc_1", tool_args={"path": "a"}
    )
    message = Message.from_draft(
        draft, conversation_id=uuid4(), sequence=2, agent_run_id=uuid4()
    )
    assert message.kind is MessageKind.TOOL_CALL
    assert message.sender_user_id is None
