"""What a resumed Agent Host session is given of what was written between its turns.

A Lemma conversation is one provider session -- Claude Code's, Codex's -- and a
resumed session already holds every turn it was sent, so a turn's prompt carries
only that turn's own messages (``turn_messages``). What is written into the
conversation outside any run is in no turn: a reminder or a report delivered to
the chat (``notification_egress``), and another of the person's chats a merge
joined to this one (``folded_turns``). The in-process harness reads them from
the history (``runtime_history``). These read the same history, as the runner
assembles it, into the prompt Agent Host is sent: what the session has not seen
goes with the next turn, inside the history's bounds, and nothing it has seen
goes again.

Held messages (``held_turns``) are not here: they are written only into a
conversation that answers somebody outside the pod, and every run there runs in
this process (``runtime_resolution``), never on an Agent Host.
"""

from __future__ import annotations

from uuid import UUID, uuid7

import pytest

from app.modules.agent.domain.entities import (
    Agent,
    AgentRun,
    Conversation,
    MessageRole,
)
from app.modules.agent.domain.folded_turns import (
    FOLDED_CHAT_CUT,
    FOLDED_CHAT_ENDS,
    FOLDED_CHAT_OPENS,
)
from app.modules.agent.domain.value_objects import ConversationStatus, ConversationType
from app.modules.agent.infrastructure.harnesses.remote_payload import run_start_payload
from app.modules.agent.domain.runtime_history import (
    MAX_FOLDED_MESSAGES,
    MAX_UNATTACHED_MESSAGES,
)
from app.modules.agent.tests.unit.runtime_history_fakes import StoredConversation
from app.modules.agent.tools.context import BaseAgentContext

pytestmark = pytest.mark.unit

USER, AGENT = MessageRole.USER, MessageRole.ASSISTANT
POD_ID = uuid7()


async def _prompt(
    chat: StoredConversation,
    run: AgentRun,
    *,
    last_seen: UUID | None,
    seen_through: int | None = None,
    carries_history: bool = False,
) -> str:
    """The prompt ``run`` is dispatched with, to a session whose newest run
    was ``last_seen``."""
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
        carries_history=carries_history,
        session_last_run_id=last_seen,
        session_seen_through=seen_through,
    )
    return str(payload["prompt"]["user_prompt"])


def _in_order(prompt: str, *lines: str) -> bool:
    places = [prompt.find(line) for line in lines]
    return -1 not in places and places == sorted(places)


async def test_a_reminder_sent_between_turns_reaches_the_resumed_session():
    """Their "yes" answers a question only the reminder asked."""
    chat = StoredConversation()
    booked = chat.turn("Book me a table for four on Friday.", "Booked: Friday at 8.")
    chat.notified("The restaurant wants a deposit by noon. Shall I pay it?")
    current = chat.turn("Yes, pay it.")

    prompt = await _prompt(chat, current, last_seen=booked.id)

    assert _in_order(prompt, "wants a deposit by noon", "Yes, pay it."), prompt
    # The session holds the turn before, and is not sent it again.
    assert "Book me a table" not in prompt


async def test_a_chat_joined_between_turns_reaches_it_as_another_chats():
    chat = StoredConversation()
    greeted = chat.turn("Hi from my laptop", "Hello.")
    chat.folded(
        (USER, "Hi from my phone, order 4471"), (AGENT, "Order 4471 ships Monday.")
    )
    current = chat.turn("So when does it arrive?")

    prompt = await _prompt(chat, current, last_seen=greeted.id)

    assert _in_order(
        prompt,
        FOLDED_CHAT_OPENS,
        "Hi from my phone, order 4471",
        "Order 4471 ships Monday.",
        FOLDED_CHAT_ENDS,
        "So when does it arrive?",
    ), prompt
    assert "Hi from my laptop" not in prompt


async def test_what_the_session_already_holds_is_not_sent_again():
    """Each line in no run goes with the first turn after it, and only that one."""
    chat = StoredConversation()
    opened = chat.turn("Hi", "Hello.")
    chat.notified("Your weekly report is ready.")
    first = chat.turn("Send it to Priya.")
    chat.notified("Priya opened the report.")
    second = chat.turn("Did she reply?")
    third = chat.turn("Thanks.")

    after_first = await _prompt(chat, first, last_seen=opened.id)
    after_second = await _prompt(chat, second, last_seen=first.id)
    after_third = await _prompt(chat, third, last_seen=second.id)

    assert "weekly report is ready" in after_first
    # Written after this turn began: the next one carries it.
    assert "Priya opened" not in after_first
    assert "Priya opened" in after_second
    assert "weekly report is ready" not in after_second
    for line in ("weekly report is ready", "Priya opened", "Did she reply?"):
        assert line not in after_third, line


async def test_a_reminder_that_came_while_the_last_turn_worked_goes_with_the_next():
    """Too late for the turn it arrived during, whose history was already read;
    so the session has not seen it."""
    chat = StoredConversation()
    working = chat.turn("Draft the brief.")
    chat.notified("Reminder: the brief is due at five.")
    chat.answer(working, "Drafted.")
    current = chat.turn("Is it ready to send?")

    during = await _prompt(chat, working, last_seen=None)
    prompt = await _prompt(chat, current, last_seen=working.id)

    assert "due at five" not in during
    assert _in_order(prompt, "due at five", "Is it ready to send?"), prompt
    assert "Draft the brief." not in prompt


async def test_a_followup_reads_the_reminder_where_it_came():
    """A follow-up has nothing of its own when it goes out: it answers what
    was typed while the last turn worked, and a reminder between those lines
    is read between them. The turn after it is sent none of it again."""
    chat = StoredConversation()
    working = chat.turn("Refactor the parser.")
    followup = chat.followup()
    chat.said_meanwhile(working, "Keep the old API.", into=followup)
    chat.notified("Reminder: code freeze at six.")
    chat.said_meanwhile(working, "And add tests.", into=followup)
    chat.answer(working, "Parser refactored.")

    prompt = await _prompt(chat, followup, last_seen=working.id)

    assert _in_order(
        prompt, "Keep the old API.", "code freeze at six", "And add tests."
    ), prompt
    assert "Refactor the parser." not in prompt
    assert "Parser refactored." not in prompt

    chat.answer(followup, "Old API kept, tests added.")
    after = await _prompt(chat, chat.turn("Ship it."), last_seen=followup.id)
    assert "code freeze" not in after
    assert "Keep the old API." not in after


async def test_the_bounds_are_the_historys():
    """No more than the in-process harness would read: the newest reminders,
    and the newest of a long chat folded in, said to be cut."""
    chat = StoredConversation()
    opened = chat.turn("Hi", "Hello.")
    for index in range(MAX_UNATTACHED_MESSAGES + 2):
        chat.notified(f"Reminder number {index:02d}.")
    chat.folded(
        *[(USER, f"Old line {index:02d}.") for index in range(MAX_FOLDED_MESSAGES + 1)]
    )
    current = chat.turn("What did I miss?")

    prompt = await _prompt(chat, current, last_seen=opened.id)

    assert "Reminder number 00." not in prompt
    assert "Reminder number 01." not in prompt
    assert f"Reminder number {MAX_UNATTACHED_MESSAGES + 1:02d}." in prompt
    assert "Old line 00." not in prompt
    assert f"Old line {MAX_FOLDED_MESSAGES:02d}." in prompt
    assert FOLDED_CHAT_OPENS + FOLDED_CHAT_CUT in prompt


async def test_a_session_whose_last_run_cannot_be_placed_is_sent_all_of_it():
    """Older than every run the history carries, or unknown: nothing in the
    history can be shown to be in the session already."""
    chat = StoredConversation()
    chat.notified("Your weekly report is ready.")
    chat.turn("Hi", "Hello.")
    chat.notified("Priya opened the report.")
    current = chat.turn("Anything new?")

    for last_seen in (None, uuid7()):
        prompt = await _prompt(chat, current, last_seen=last_seen)
        assert _in_order(
            prompt, "weekly report is ready", "Priya opened", "Anything new?"
        ), prompt
        assert "Hello." not in prompt


async def test_a_session_that_cannot_resume_is_told_it_all_once():
    chat = StoredConversation()
    chat.turn("Can you help today?", "Of course.")
    chat.notified("Your weekly report is ready.")
    current = chat.turn("Send it.")

    prompt = await _prompt(chat, current, last_seen=None, carries_history=True)

    assert _in_order(
        prompt,
        "Can you help today?",
        "Of course.",
        "weekly report is ready",
        "Send it.",
    ), prompt
    assert prompt.count("weekly report is ready") == 1


async def test_what_came_between_a_followups_dispatch_and_its_first_word_goes_next():
    """A follow-up has nothing of its own when it is dispatched, so where its
    prompt's history ended is recorded then, not read later off its first
    message. A reminder landing in between went with neither turn."""
    chat = StoredConversation()
    working = chat.turn("Refactor the parser.")
    followup = chat.followup()
    chat.said_meanwhile(working, "Keep the old API.", into=followup)
    chat.answer(working, "Parser refactored.")
    dispatched = await chat.history_for(followup)
    seen_through = max(message.sequence for message in dispatched)
    chat.notified("Reminder: code freeze at six.")
    chat.answer(followup, "Old API kept.")
    current = chat.turn("Ship it.")

    prompt = await _prompt(
        chat, current, last_seen=followup.id, seen_through=seen_through
    )

    assert _in_order(prompt, "code freeze at six", "Ship it."), prompt
    assert "Keep the old API." not in prompt


async def test_a_last_prompt_collapsed_out_of_the_history_still_places_the_session():
    """Its run's messages are gone from the history, so its start cannot be
    read there; where its prompt ran to was recorded when it went out."""
    chat = StoredConversation()
    seen = chat.turn("Hi", "Hello.")
    chat.notified("Your weekly report is ready.")
    seen_through = max(message.sequence for message in chat.messages)
    for index in range(8):
        chat.turn(f"in-process question {index}", f"answer {index}")
    chat.notified("Priya opened the report.")
    current = chat.turn("Did she reply?")

    prompt = await _prompt(chat, current, last_seen=seen.id, seen_through=seen_through)

    assert "Priya opened" in prompt
    assert "weekly report is ready" not in prompt
