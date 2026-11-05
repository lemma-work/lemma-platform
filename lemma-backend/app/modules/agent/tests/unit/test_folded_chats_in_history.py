"""What a contact merge folds in reaches the agent's next run, as another chat's.

When two contacts turn out to be one person, what they saw of the conversation
that lost its seat is copied into the one that kept it, in no run
(``contracts/folded_history``). History is read by run, so the agent
answering the kept conversation read none of it -- neither the person's words
in the other chat nor its own answers there. These pin the assembly: the
copies are read where the merge put them, between notices saying whose they
are, under a bound of their own, inside the bounds every run-less message
keeps, and with no sending time, since theirs is the merge's. The query is
held to the same contract against Postgres in ``test_runtime_history_load_e2e``.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.modules.agent.domain.entities import (
    Message,
    MessageRole,
    RuntimeHistoryWindow,
)
from app.modules.agent.domain.folded_turns import (
    FOLDED_CHAT_CUT,
    FOLDED_CHAT_ENDS,
    FOLDED_CHAT_OPENS,
    FOLDED_FROM_KEY,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)
from app.modules.agent.services.runtime_history import (
    MAX_FOLDED_MESSAGES,
    MAX_HISTORY_AGENT_RUNS,
    assemble_runtime_history,
    without_private_runs,
)
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


def _texts(history: list[Message]) -> list[str | None]:
    return [message.text for message in history]


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
    # Where the merge put it, and said to be the other chat's: its last line
    # is not the newest thing they said here.
    assert _texts(history) == [
        "Hi from my laptop",
        "Hello.",
        FOLDED_CHAT_OPENS,
        "Hi from my phone",
        "Hello again.",
        FOLDED_CHAT_ENDS,
        "Still me",
    ]
    model_history, prompt = history_and_prompt(history)
    assert prompt is not None and prompt.endswith("Still me")
    # Their words read as theirs, the agent's answer there as its own.
    assert _said(model_history) == ["Hello.", "Hello again."]
    laptop, opens, phone, ends = _asked(model_history)
    assert laptop.startswith("<notes>Sent ") and laptop.endswith("Hi from my laptop")
    # The copy was written when the merge ran, which is not when it was sent.
    assert phone == "Hi from my phone"
    assert (opens, ends) == (FOLDED_CHAT_OPENS, FOLDED_CHAT_ENDS)


async def test_a_long_chat_folded_in_brings_its_newest_lines_and_says_so():
    chat = StoredConversation()
    chat.follow_up("We have two left.")
    chat.turn("Is the blue one in stock?", "Let me check.")
    chat.folded(
        *[
            (USER if index % 2 == 0 else AGENT, f"line {index}")
            for index in range(MAX_FOLDED_MESSAGES + 5)
        ]
    )
    current = chat.turn("And now?")

    history = await chat.history_for(current)

    texts = _texts(history)
    carried = [text for text in texts if text and text.startswith("line ")]
    assert carried == [f"line {index}" for index in range(5, MAX_FOLDED_MESSAGES + 5)]
    assert FOLDED_CHAT_OPENS + FOLDED_CHAT_CUT in texts
    # A bound of its own: the notification is not crowded out by the copies.
    assert "We have two left." in texts


async def test_each_chat_folded_in_is_framed_on_its_own():
    chat = StoredConversation()
    chat.turn("Hi from my laptop", "Hello.")
    chat.folded((USER, "Hi from my phone"), (AGENT, "Hello again."))
    chat.folded((USER, "Hi from my tablet"), (AGENT, "Hello once more."))
    current = chat.turn("Still me")

    history = await chat.history_for(current)

    assert _texts(history)[2:-1] == [
        FOLDED_CHAT_OPENS,
        "Hi from my phone",
        "Hello again.",
        FOLDED_CHAT_ENDS,
        FOLDED_CHAT_OPENS,
        "Hi from my tablet",
        "Hello once more.",
        FOLDED_CHAT_ENDS,
    ]


async def test_folded_lines_keep_the_bounds_every_run_less_message_keeps():
    chat = StoredConversation()
    chat.folded((USER, "Before everything the prompt still carries"))
    # Enough runs that the oldest are no longer carried at all.
    for index in range(MAX_HISTORY_AGENT_RUNS):
        chat.turn(f"question {index}", f"answer {index}")
    chat.folded((USER, "Folded in since the last answer"))
    current = chat.turn("And now?")
    chat.folded((USER, "Folded in while this turn was being answered"))

    history = await chat.history_for(current)

    texts = _texts(history)
    assert "Folded in since the last answer" in texts
    assert "question 0" not in texts
    assert "Before everything the prompt still carries" not in texts
    assert "Folded in while this turn was being answered" not in texts
    assert texts[-1] == "And now?"


async def test_a_strangers_run_reads_the_chat_folded_in_and_never_a_members_note():
    chat = StoredConversation()
    chat.turn("What's your day rate?", "It's 50k for the week.")
    chat.folded((USER, "Can you do 35k?"), (AGENT, "Let me ask."))
    chat.turn("Our floor is 40k -- don't go below.", "Understood.", note=True)
    current = chat.turn("So, 35k?")

    history = without_private_runs(
        await chat.history_for(current), chat.runs, current_run_id=current.id
    )

    assert _lines(history) == [
        (USER, "What's your day rate?"),
        (AGENT, "It's 50k for the week."),
        (USER, "Can you do 35k?"),
        (AGENT, "Let me ask."),
        (USER, "So, 35k?"),
    ]


async def test_a_stretch_the_channel_read_empties_is_not_framed():
    """A copied follow-up whose line is gone is dropped as the channel is read
    in; the stretch it was all of leaves no notices behind."""
    chat = StoredConversation()
    chat.turn("Hi from my laptop", "Hello.")
    # A member's line in the other chat, as the merge copies it: the follow-up
    # with its marks, and where it came from.
    chat.follow_up("We have two left.")
    line = chat.messages[-1]
    chat.messages[-1] = line.model_copy(
        update={"metadata": {**(line.metadata or {}), FOLDED_FROM_KEY: str(uuid4())}}
    )
    current = chat.turn("Still me")

    async def line_gone(
        messages: list[Message], *, background_of_runs: set[UUID]
    ) -> list[Message]:
        return [m for m in messages if not (m.metadata or {}).get("follow_up")]

    messages = await assemble_runtime_history(
        chat,
        RuntimeHistoryWindow(
            runs=[run.model_copy(update={"messages": []}) for run in chat.runs],
            total_runs=len(chat.runs),
            current_run=current,
        ),
        conversation_id=chat.id,
        run_id=current.id,
        read_channel=line_gone,
    )

    assert _texts(sorted(messages, key=lambda m: m.sequence)) == [
        "Hi from my laptop",
        "Hello.",
        "Still me",
    ]
