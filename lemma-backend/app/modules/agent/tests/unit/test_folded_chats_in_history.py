"""What a contact merge folds in reaches the agent's next run.

When two contacts turn out to be one person, what they saw of the conversation
that lost its seat is copied into the one that kept it, in no run
(``contracts/folded_history``). History is read by run, so the agent
answering the kept conversation read none of it -- neither the person's words
in the other chat nor its own answers there.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.entities import Message, MessageRole
from app.modules.agent.tests.unit.runtime_history_fakes import StoredConversation

pytestmark = pytest.mark.unit

USER, AGENT = MessageRole.USER, MessageRole.ASSISTANT


def _lines(history: list[Message]) -> list[tuple[MessageRole, str | None]]:
    """Who said what, without the notices the history writes."""
    return [
        (message.role, message.text)
        for message in history
        if not (message.metadata or {}).get("synthetic")
    ]


async def test_the_chat_a_merge_folded_in_is_read_before_the_new_message():
    chat = StoredConversation()
    chat.turn("Hi from my laptop", "Hello.")
    chat.folded((USER, "Hi from my phone"), (AGENT, "Hello again."))
    current = chat.turn("Still me")

    history = await chat.history_for(current)

    assert _lines(history) == [
        (USER, "Hi from my laptop"),
        (AGENT, "Hello."),
        (USER, "Hi from my phone"),
        (AGENT, "Hello again."),
        (USER, "Still me"),
    ]
