"""A member's words in a contact's conversation reach the agent as theirs.

A member who writes to a contact puts their message in the contact's
conversation, on the agent's side of it. Rendered plainly it reads to the
agent as something it said itself -- and in QA the agent then told the contact
that the opening hours a member had given them were its own unverified guess.
"""

from __future__ import annotations

from uuid import uuid7

from pydantic_ai.messages import ModelResponse, TextPart

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    MEMBER_WROTE,
    history_and_prompt,
)


def _message(sequence, role, kind, text, metadata=None) -> Message:
    return Message.create(
        conversation_id=uuid7(),
        sequence=sequence,
        agent_run_id=None,
        role=role,
        kind=kind,
        text=text,
        metadata=metadata,
    )


def _said(history) -> list[str]:
    return [
        part.content
        for message in history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]


def test_a_members_follow_up_is_marked_as_theirs_and_the_agents_own_words_are_not():
    history, prompt = history_and_prompt(
        [
            _message(1, MessageRole.USER, MessageKind.TEXT, "When are you open?"),
            _message(2, MessageRole.ASSISTANT, MessageKind.TEXT, "I'll ask."),
            _message(
                3,
                MessageRole.ASSISTANT,
                MessageKind.NOTIFICATION,
                "We open 10 to 7.",
                {"follow_up": True, "sent_by_user_id": str(uuid7())},
            ),
            _message(4, MessageRole.USER, MessageKind.TEXT, "Thanks!"),
        ]
    )

    assert prompt is not None and prompt.endswith("Thanks!")
    assert _said(history) == ["I'll ask.", MEMBER_WROTE + "We open 10 to 7."]
