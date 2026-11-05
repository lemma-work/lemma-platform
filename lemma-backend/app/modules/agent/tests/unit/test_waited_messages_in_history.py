"""A message a later turn answers is read after the run it waited for.

Said on WhatsApp while a run answered an email, a message is not taken into
that run (``domain/run_places``): it is stored where it was typed, inside the
email's run, and the follow-up turn claims it. The follow-up has to read it as
what it is answering -- after the email's answer, as its own prompt -- not
above an answer it played no part in. A paused run's queue is already after
the question the run paused on, and stays where it is.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)

pytestmark = pytest.mark.unit

_CONVERSATION = uuid4()


def _message(sequence, run_id, role, text, **metadata) -> Message:
    return Message(
        conversation_id=_CONVERSATION,
        sequence=sequence,
        agent_run_id=run_id,
        role=role,
        kind=MessageKind.TEXT,
        text=text,
        metadata=metadata or None,
    )


def test_a_message_left_for_a_follow_up_is_its_prompt_after_the_run_it_waited_for():
    email_run, whatsapp_run = uuid4(), uuid4()
    messages = [
        _message(1, email_run, MessageRole.USER, "Was the invoice paid?"),
        _message(
            2,
            email_run,
            MessageRole.USER,
            "Move Thursday's call?",
            during_active_run=True,
            steered_into_run=str(whatsapp_run),
        ),
        _message(3, email_run, MessageRole.ASSISTANT, "Paid on the 3rd."),
    ]

    history, prompt = history_and_prompt(messages, current_run_id=whatsapp_run)

    assert prompt is not None and "Move Thursday's call?" in prompt
    assert "Move Thursday's call?" not in str(history)
    assert "Paid on the 3rd." in str(history)


def test_once_answered_it_stays_between_the_two_answers():
    """Read the same way by every later turn, so the prompt's prefix holds."""
    email_run, whatsapp_run, later_run = uuid4(), uuid4(), uuid4()
    messages = [
        _message(1, email_run, MessageRole.USER, "Was the invoice paid?"),
        _message(
            2,
            email_run,
            MessageRole.USER,
            "Move Thursday's call?",
            during_active_run=True,
            steered_into_run=str(whatsapp_run),
        ),
        _message(3, email_run, MessageRole.ASSISTANT, "Paid on the 3rd."),
        _message(4, whatsapp_run, MessageRole.ASSISTANT, "Moved it to Friday."),
        _message(5, later_run, MessageRole.USER, "Thanks."),
    ]

    history, _ = history_and_prompt(messages, current_run_id=later_run)

    read = str(history)
    assert (
        read.index("Paid on the 3rd.")
        < read.index("Move Thursday's call?")
        < read.index("Moved it to Friday.")
    )


def test_a_message_steered_into_its_own_run_stays_where_it_was_typed():
    run = uuid4()
    messages = [
        _message(1, run, MessageRole.USER, "Was the invoice paid?"),
        _message(
            2,
            run,
            MessageRole.USER,
            "And the second one?",
            during_active_run=True,
            steered_into_run=str(run),
        ),
        _message(3, run, MessageRole.ASSISTANT, "Both are paid."),
        _message(4, uuid4(), MessageRole.USER, "Great."),
    ]

    history, _ = history_and_prompt(messages)

    read = str(history)
    assert read.index("And the second one?") < read.index("Both are paid.")
