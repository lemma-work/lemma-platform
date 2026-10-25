"""What a contact said while a person had them reaches the agent's next run.

A contact's turn starts no run while a member answers them by hand, or past
the organization's contacts cap. Their message is kept in no run, and history
is read by run, so once the hold ended the agent answered their newest message
as if there were no earlier thread. These pin the assembly: where held
messages land, the bounds they share with notifications, and that a member's
private note stays out of a stranger's run while the stranger's own words do
not. The query itself is held to the same contract against Postgres in
``test_runtime_history_load_e2e``.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.modules.agent.domain.entities import Message, MessageRole
from app.modules.agent.domain.held_turns import held_messages
from app.modules.agent.domain.private_notes import (
    PRIVATE_NOTE_FROM_KEEPER_LABEL,
    PRIVATE_NOTE_REQUEST_KEY,
    WRITTEN_BY_KEEPER_LABEL,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)
from app.modules.agent.services.runtime_history import (
    MAX_HISTORY_AGENT_RUNS,
    MAX_UNATTACHED_MESSAGES,
    without_private_runs,
)
from app.modules.agent.tests.unit.runtime_history_fakes import StoredConversation

pytestmark = pytest.mark.unit

USER, AGENT = MessageRole.USER, MessageRole.ASSISTANT


def _lines(history: list[Message]) -> list[tuple[MessageRole, str | None]]:
    """Who said what, without the notices history elision writes."""
    return [
        (message.role, message.text)
        for message in history
        if not (message.metadata or {}).get("synthetic")
    ]


def _asked(model_history: list[ModelMessage]) -> list[str]:
    """What the model reads as people's messages, as it reads them."""
    return [
        str(part.content)
        for request in model_history
        if isinstance(request, ModelRequest)
        for part in request.parts
        if isinstance(part, UserPromptPart)
    ]


def _said(model_history: list[ModelMessage]) -> list[str]:
    """What the model reads as its own words."""
    return [
        part.content
        for response in model_history
        if isinstance(response, ModelResponse)
        for part in response.parts
        if isinstance(part, TextPart)
    ]


def test_a_held_turn_keeps_their_message_and_what_they_were_told_both_marked():
    theirs, told = held_messages(
        "Is anybody there?",
        {"sender_display_name": "Dana"},
        reply="A person from the shop will reply here.",
    )

    assert (theirs.role, theirs.text, theirs.metadata) == (
        USER,
        "Is anybody there?",
        {"sender_display_name": "Dana"},
    )
    assert (told.role, told.text, told.metadata) == (
        AGENT,
        "A person from the shop will reply here.",
        {"handed_off": True},
    )
    assert theirs.state.held_for_a_person and told.state.held_for_a_person
    # Told nothing: only their message is kept.
    assert [draft.text for draft in held_messages("Hello?", None, reply=None)] == [
        "Hello?"
    ]


async def test_what_they_said_while_a_member_had_them_is_read_before_their_new_message():
    chat = StoredConversation()
    chat.turn("Is the blue one in stock?", "Let me check.")
    chat.follow_up("We have two left.")
    chat.held("Can you hold one for me?")
    chat.held("I can pick it up on Friday.")
    chat.unmarked("A line nothing marked as held")
    current = chat.turn("Hello? Is that a yes?")

    history = await chat.history_for(current)

    assert _lines(history) == [
        (USER, "Is the blue one in stock?"),
        (AGENT, "Let me check."),
        (AGENT, "We have two left."),
        (USER, "Can you hold one for me?"),
        (USER, "I can pick it up on Friday."),
        (USER, "Hello? Is that a yes?"),
    ]
    # The model reads them as theirs, before the message it is answering.
    model_history, prompt = history_and_prompt(history)
    assert prompt is not None and prompt.endswith("Hello? Is that a yes?")
    asked = _asked(model_history)
    assert asked[-2].endswith("Can you hold one for me?")
    assert asked[-1].endswith("I can pick it up on Friday.")


async def test_what_they_were_told_in_place_of_an_answer_comes_along():
    chat = StoredConversation()
    chat.held("Is the shop open?", reply="A person from the shop will reply here.")
    chat.held("Hello?")
    current = chat.turn("Are you there now?")

    history = await chat.history_for(current)

    # Their first words came before any run: nothing was cut, so nothing
    # bounds them from below.
    assert _lines(history) == [
        (USER, "Is the shop open?"),
        (AGENT, "A person from the shop will reply here."),
        (USER, "Hello?"),
        (USER, "Are you there now?"),
    ]
    # Said to them as the bot's, so read as the agent's own words.
    assert _said(history_and_prompt(history)[0]) == [
        "A person from the shop will reply here."
    ]


async def test_held_lines_are_bounded_as_notifications_are():
    chat = StoredConversation()
    chat.held("Before everything the prompt still carries")
    # Enough runs that the oldest are no longer carried at all.
    for index in range(MAX_HISTORY_AGENT_RUNS):
        chat.turn(f"question {index}", f"answer {index}")
    chat.held("Since the last answer")
    current = chat.turn("And now?")
    chat.held("Written while this turn was being answered")

    history = await chat.history_for(current)

    texts = [text for _, text in _lines(history)]
    assert "Since the last answer" in texts
    # Older than the oldest run carried: the history was cut, and it went with it.
    assert "question 0" not in texts
    assert "Before everything the prompt still carries" not in texts
    # After the turn being answered: never placed after the message it answers.
    assert "Written while this turn was being answered" not in texts
    assert texts[-1] == "And now?"


async def test_held_lines_past_the_bound_are_cut_and_the_cut_is_said():
    """A member who answered by hand for a while leaves more run-less lines than
    are carried. The newest are kept, and the agent is told the thread starts
    earlier than what it reads, rather than answering as if it did not."""
    chat = StoredConversation()
    chat.turn("Hello", "Hi, how can I help?")
    for index in range(MAX_UNATTACHED_MESSAGES + 3):
        chat.held(f"held {index}")
    current = chat.turn("Are you back?")

    history = await chat.history_for(current)

    texts = [text for _, text in _lines(history)]
    held = [text for text in texts if text and text.startswith("held ")]
    assert held == [f"held {index}" for index in range(3, MAX_UNATTACHED_MESSAGES + 3)]
    notices = [
        message
        for message in history
        if (message.metadata or {}).get("summary_kind") == "unattached_messages_cut"
    ]
    assert len(notices) == 1
    # Read immediately before the oldest line it stands in front of.
    at = history.index(notices[0])
    assert history[at + 1].text == "held 3"


async def test_nothing_is_said_to_be_cut_when_nothing_was():
    chat = StoredConversation()
    chat.turn("Hello", "Hi")
    for index in range(MAX_UNATTACHED_MESSAGES):
        chat.held(f"held {index}")
    current = chat.turn("Back?")

    history = await chat.history_for(current)

    assert not any(
        (message.metadata or {}).get("summary_kind") == "unattached_messages_cut"
        for message in history
    )


async def test_a_stranger_reads_their_own_held_words_and_never_a_members_note():
    chat = StoredConversation()
    chat.turn("What's your day rate?", "It's 50k for the week.")
    # Whatever the caller passed, a held message is the outsider's words: it
    # never carries what would make it read as the member's, or drop as a note.
    chat.held(
        "Can you do 35k?",
        **{PRIVATE_NOTE_REQUEST_KEY: True, "written_in_lemma": "outsiders"},
    )
    chat.turn("Our floor is 40k -- don't go below.", "Understood.", note=True)
    current = chat.turn("So, 35k?")

    history = without_private_runs(
        await chat.history_for(current), chat.runs, current_run_id=current.id
    )

    assert _lines(history) == [
        (USER, "What's your day rate?"),
        (AGENT, "It's 50k for the week."),
        (USER, "Can you do 35k?"),
        (USER, "So, 35k?"),
    ]
    # Read as theirs: under no label a member's own words carry.
    [held] = [text for text in _asked(history_and_prompt(history)[0]) if "35k" in text]
    assert held.endswith("Can you do 35k?")
    assert PRIVATE_NOTE_FROM_KEEPER_LABEL not in held
    assert WRITTEN_BY_KEEPER_LABEL not in held
