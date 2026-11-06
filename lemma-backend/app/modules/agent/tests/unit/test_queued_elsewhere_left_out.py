"""What waits for a turn of its own is read by that turn only.

Said on WhatsApp while a run answers an email, a message is owed by the
follow-up turn that claims it (``domain/run_places``). The email's run must not
read it at all: it would take it in without claiming it, the run log would
count it read, and the follow-up would find nothing left to answer.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.run_places import (
    ASKED_IN_PLACE_KEY,
    without_queued_elsewhere,
)
from app.modules.agent.domain.run_state import RunPrivacy
from app.modules.agent.domain.value_objects import (
    MessageKind,
    MessageRole,
    QueueState,
)

pytestmark = pytest.mark.unit

_CONVERSATION = uuid4()
_EMAIL, _WHATSAPP = uuid4(), uuid4()


def _said(
    sequence: int,
    run_id: UUID,
    text: str,
    *,
    at: UUID | None = None,
    queue: QueueState = QueueState.NONE,
    steered_into: UUID | None = None,
    note: bool = False,
    role: MessageRole = MessageRole.USER,
) -> Message:
    return Message(
        conversation_id=_CONVERSATION,
        sequence=sequence,
        agent_run_id=run_id,
        role=role,
        kind=MessageKind.TEXT,
        text=text,
        # Where it was asked, as the server stores it with the row.
        metadata={ASKED_IN_PLACE_KEY: str(at)} if at else None,
        state=MessageState(
            queue_state=queue,
            steered_into_run_id=steered_into,
            asked_at_source=at,
            private_note=note,
        ),
    )


def _texts(messages: list[Message]) -> list[str | None]:
    return [message.text for message in messages]


def test_the_run_it_waited_behind_does_not_read_a_message_from_another_place():
    email_run = uuid4()
    messages = [
        _said(1, email_run, "Was the invoice paid?", at=_EMAIL),
        _said(
            2, email_run, "Move Thursday's call?", at=_WHATSAPP, queue=QueueState.QUEUED
        ),
    ]

    kept = without_queued_elsewhere(
        messages, run_id=email_run, run=RunPrivacy(asked_at_source=_EMAIL)
    )

    assert _texts(kept) == ["Was the invoice paid?"]


def test_a_queued_message_from_the_runs_own_place_or_none_stays():
    email_run = uuid4()
    messages = [
        _said(1, email_run, "Was the invoice paid?", at=_EMAIL),
        _said(2, email_run, "And the one before?", at=_EMAIL, queue=QueueState.QUEUED),
        _said(3, email_run, "Typed in Lemma", queue=QueueState.QUEUED),
    ]

    kept = without_queued_elsewhere(
        messages, run_id=email_run, run=RunPrivacy(asked_at_source=_EMAIL)
    )

    assert _texts(kept) == [
        "Was the invoice paid?",
        "And the one before?",
        "Typed in Lemma",
    ]


def test_the_follow_up_that_claimed_it_reads_it_and_so_does_every_later_run():
    email_run, whatsapp_run, later_run = uuid4(), uuid4(), uuid4()
    messages = [
        _said(1, email_run, "Was the invoice paid?", at=_EMAIL),
        _said(
            2,
            email_run,
            "Move Thursday's call?",
            at=_WHATSAPP,
            queue=QueueState.STEERED,
            steered_into=whatsapp_run,
        ),
    ]

    for run_id, run in (
        (whatsapp_run, RunPrivacy(asked_at_source=_WHATSAPP)),
        (later_run, RunPrivacy(asked_at_source=_EMAIL)),
    ):
        assert _texts(without_queued_elsewhere(messages, run_id=run_id, run=run)) == [
            "Was the invoice paid?",
            "Move Thursday's call?",
        ]


def test_a_note_waiting_beside_a_platform_answer_is_left_for_its_own_turn():
    run = uuid4()
    messages = [
        _said(1, run, "Was the invoice paid?", at=_EMAIL),
        _said(2, run, "only for me: chase them", note=True, queue=QueueState.QUEUED),
    ]

    kept = without_queued_elsewhere(
        messages, run_id=run, run=RunPrivacy(asked_at_source=_EMAIL)
    )

    assert _texts(kept) == ["Was the invoice paid?"]


def test_what_is_not_a_queued_persons_message_is_never_left_out():
    run = uuid4()
    messages = [
        _said(1, run, "Was the invoice paid?", at=_EMAIL),
        _said(
            2,
            run,
            "Paid on the 3rd.",
            role=MessageRole.ASSISTANT,
            queue=QueueState.QUEUED,
        ),
        _said(3, run, "Earlier, from WhatsApp", at=_WHATSAPP),
    ]

    kept = without_queued_elsewhere(
        messages, run_id=run, run=RunPrivacy(asked_at_source=_EMAIL)
    )

    assert len(kept) == 3
