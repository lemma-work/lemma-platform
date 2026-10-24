"""What a resumed Agent Host session is given of what was written between its turns.

A Lemma conversation is one provider session -- Claude Code's, Codex's -- and a
resumed session already holds every turn it was sent, so a turn's prompt carries
only that turn's own messages (``remote_payload._turn_messages``). What is
written into the conversation outside any run is in no turn: a reminder or a
report delivered to the chat (``notification_egress``), and another of the
person's chats a merge joined to this one (``folded_turns``). The in-process
harness reads them from the history (``runtime_history``). These read the same
history, as the runner assembles it, into the prompt Agent Host is sent.
"""

from __future__ import annotations

from uuid import uuid7

import pytest

from app.modules.agent.domain.entities import (
    Agent,
    AgentRun,
    Conversation,
    MessageRole,
)
from app.modules.agent.domain.folded_turns import FOLDED_CHAT_ENDS, FOLDED_CHAT_OPENS
from app.modules.agent.domain.value_objects import ConversationStatus, ConversationType
from app.modules.agent.infrastructure.harnesses.remote_payload import run_start_payload
from app.modules.agent.tests.unit.runtime_history_fakes import StoredConversation
from app.modules.agent.tools.context import BaseAgentContext

pytestmark = pytest.mark.unit

USER, AGENT = MessageRole.USER, MessageRole.ASSISTANT
POD_ID = uuid7()


async def _resumed_prompt(chat: StoredConversation, run: AgentRun) -> str:
    """The prompt ``run`` is dispatched with to a session it resumes."""
    payload = run_start_payload(
        agent=Agent(pod_id=POD_ID, user_id=uuid7(), name="helper", instruction=""),
        conversation=Conversation(
            id=chat.id,
            pod_id=POD_ID,
            user_id=uuid7(),
            agent_id=uuid7(),
            title="between turns",
            type=ConversationType.CHAT,
            status=ConversationStatus.RUNNING,
        ),
        messages=await chat.history_for(run),
        ctx=BaseAgentContext(user_id=uuid7(), pod_id=POD_ID, conversation_id=chat.id),
        agent_run_id=run.id,
        runtime_instructions="",
        carries_history=False,
    )
    return str(payload["prompt"]["user_prompt"])


def _in_order(prompt: str, *lines: str) -> bool:
    places = [prompt.find(line) for line in lines]
    return -1 not in places and places == sorted(places)


async def test_a_reminder_sent_between_turns_reaches_the_resumed_session():
    """Their "yes" answers a question only the reminder asked."""
    chat = StoredConversation()
    chat.turn("Book me a table for four on Friday.", "Booked: Friday at eight.")
    chat.notified("The restaurant wants a deposit by noon. Shall I pay it?")
    current = chat.turn("Yes, pay it.")

    prompt = await _resumed_prompt(chat, current)

    assert _in_order(prompt, "wants a deposit by noon", "Yes, pay it."), prompt
    # The session holds the turn before, and is not sent it again.
    assert "Book me a table" not in prompt


async def test_a_chat_joined_between_turns_reaches_it_as_another_chats():
    chat = StoredConversation()
    chat.turn("Hi from my laptop", "Hello.")
    chat.folded(
        (USER, "Hi from my phone, order 4471"), (AGENT, "Order 4471 ships Monday.")
    )
    current = chat.turn("So when does it arrive?")

    prompt = await _resumed_prompt(chat, current)

    assert _in_order(
        prompt,
        FOLDED_CHAT_OPENS,
        "Hi from my phone, order 4471",
        "Order 4471 ships Monday.",
        FOLDED_CHAT_ENDS,
        "So when does it arrive?",
    ), prompt
    assert "Hi from my laptop" not in prompt
