"""A run that stops for an approval ends on a whole message, never a lead-in.

The person reads the agent's last words while the card waits. Those used to be
whatever preceded the call -- "Now I'll send the invoice to" -- so a run could
end mid-sentence and say what it had done only after the decision.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.agent_host_permissions import AGENT_HOST_PERMISSION_KEY
from app.modules.agent.domain.approval_message import (
    APPROVAL_MESSAGE_KEY,
    with_approval_message,
)
from app.modules.agent.domain.run_budget import BudgetDimension, BudgetExhausted
from app.modules.agent.domain.run_budget_pause import budget_pause_tool_args
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.services.run_event_pump import _split_message_event

pytestmark = pytest.mark.unit


def _approval(**args: object) -> MessageDraft:
    return MessageDraft.of_tool_call(
        tool_name="request_approval",
        tool_call_id="call_1",
        tool_args={"tool_name": "send_email", "args": {"to": "a@example.com"}, **args},
    )


def test_the_agents_message_is_stored_before_the_card():
    said = (
        "I drafted the invoice. Sending it needs your approval: it emails the client."
    )
    message, card = with_approval_message(_approval(title="Send invoice", message=said))

    assert (message.kind, message.text) == (MessageKind.TEXT, said)
    assert message.metadata == {
        "is_final_answer": False,
        APPROVAL_MESSAGE_KEY: "call_1",
    }
    assert card.kind is MessageKind.TOOL_CALL


def test_a_call_with_no_message_still_ends_on_a_whole_sentence():
    message, _card = with_approval_message(_approval(title="Send invoice"))

    assert message.text == "I need your approval before I go on: Send invoice."


@pytest.mark.parametrize(
    "raised_by_lemma",
    [
        budget_pause_tool_args(
            BudgetExhausted(dimension=BudgetDimension.MODEL_REQUESTS, spent=200)
        ),
        {"title": "Run a command", AGENT_HOST_PERMISSION_KEY: {"request_id": "r"}},
    ],
)
def test_a_card_lemma_raised_gets_no_words_put_in_the_agents_mouth(raised_by_lemma):
    draft = MessageDraft.of_tool_call(
        tool_name="request_approval", tool_call_id="x", tool_args=raised_by_lemma
    )

    assert with_approval_message(draft) == [draft]


def test_every_reader_of_the_run_sees_the_message_before_the_card():
    """Split before anything reads the event, as reasoning is: the transcript,
    a surface's narration and the run's output all see the same two messages."""
    event = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=_approval(title="Send invoice", message="Done drafting; may I send it?"),
        agent_run_id=uuid4(),
    )

    split = _split_message_event(event)

    assert [one.data.kind for one in split] == [MessageKind.TEXT, MessageKind.TOOL_CALL]
    assert split[0].data.text == "Done drafting; may I send it?"
