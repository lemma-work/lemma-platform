"""Typing past a card on a surface: an answer, or a new instruction?

The composer stays enabled while a conversation is WAITING, so somebody can
type straight past a question. Every such message used to be taken as the
answer to whatever was pending, however old — so a question nobody tapped
swallowed the next instruction anybody sent, recorded it as the answer, and
started no run for it. Their request was simply lost.

Only surfaces behaved that way. A new message from the web or the CLI
supersedes the stale pause and carries on
(`ConversationTurns.start` -> `supersede_stale_pending_interactions`), which is
the behaviour these tests pin surfaces to.

Returning False here is what hands the message to that path, so False is the
assertion that matters: it means the pause gets marked unanswered, the agent is
told, and the person's actual instruction runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.pending_interaction_resume import (
    maybe_resume_pending_interaction,
)

A_QUESTION = {
    "tool_call_id": "call-the-question",
    "kind": "ask_user",
    "tool_args": {
        "request": {
            "questions": [
                {
                    "header": "report",
                    "question": "Which report?",
                    "options": [{"label": "Weekly summary"}, {"label": "Full ledger"}],
                }
            ]
        }
    },
    "agent_run_id": uuid4(),
}

AN_APPROVAL = {
    "tool_call_id": "call-the-approval",
    "kind": "request_approval",
    "tool_args": {"tool_name": "pod_write_record", "title": "Write a record"},
    "agent_run_id": uuid4(),
}

#: What somebody types when they have moved on from the card.
A_NEW_INSTRUCTION = "Create a table called approvals_probe with a column named note."


def _service(pending: dict, *, free_text_wanted_for: str | None = None):
    """A conversation waiting on `pending`, on a surface that renders buttons."""
    conversation_id = uuid4()
    service = AsyncMock()
    service.get_pending_user_interaction.return_value = dict(pending)
    service.conversation_repository.get_conversation.return_value = SimpleNamespace(
        id=conversation_id
    )
    service.conversation_repository.get_conversation_metadata_key.return_value = (
        free_text_wanted_for
    )
    return service, conversation_id


def _context(conversation_id, platform: str = "TELEGRAM"):
    return SimpleNamespace(
        conversation_id=conversation_id,
        user_id=uuid4(),
        pod_id=uuid4(),
        platform=platform,
        agent_name=None,
    )


@pytest.mark.asyncio
async def test_typing_past_a_question_is_a_new_message() -> None:
    """The bug, in one assertion.

    A question is on screen with two buttons. Somebody ignores it and asks for
    something else. That instruction is theirs, not an answer to "Which report?".
    """
    service, conversation_id = _service(A_QUESTION)

    consumed = await maybe_resume_pending_interaction(
        _context(conversation_id), A_NEW_INSTRUCTION, conversation_service=service
    )

    assert consumed is False
    service.resolve_user_approval_internal.assert_not_awaited()


@pytest.mark.asyncio
async def test_typing_past_an_approval_is_a_new_message() -> None:
    """Same for an approval card, and it matters more.

    Anything that is not an approval word was read as a denial, so an unrelated
    instruction silently denied whatever was waiting *and* was lost itself.
    Falling through denies it too — but through the path that also tells the
    agent and runs what was asked.
    """
    service, conversation_id = _service(AN_APPROVAL)

    consumed = await maybe_resume_pending_interaction(
        _context(conversation_id), A_NEW_INSTRUCTION, conversation_service=service
    )

    assert consumed is False
    service.resolve_user_approval_internal.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_answer_typed_after_tapping_other_still_answers() -> None:
    """The affordance this must not break.

    "Other" means "I will type it", so the next message *is* the answer.
    """
    service, conversation_id = _service(
        A_QUESTION, free_text_wanted_for="call-the-question"
    )

    consumed = await maybe_resume_pending_interaction(
        _context(conversation_id), "Weekly summary", conversation_service=service
    )

    assert consumed is True
    resolved = service.resolve_user_approval_internal.await_args.kwargs
    assert resolved["approval_id"] == "call-the-question"


@pytest.mark.asyncio
async def test_other_tapped_on_a_different_question_is_not_spent_here() -> None:
    """The intent belongs to one call, not to the conversation.

    Otherwise an "Other" tapped two turns ago would go on making every later
    message an answer — the same bug with more steps.
    """
    service, conversation_id = _service(
        A_QUESTION, free_text_wanted_for="call-some-other-question"
    )

    consumed = await maybe_resume_pending_interaction(
        _context(conversation_id), A_NEW_INSTRUCTION, conversation_service=service
    )

    assert consumed is False


@pytest.mark.asyncio
async def test_typing_an_offered_option_still_answers() -> None:
    """Buttons on screen, and they typed one of the options anyway.

    That is answering, and reading it as a new instruction would be perverse.
    Matched narrowly — an offered label or its 1-based index — because the
    parser's own fallback treats *any* text as a free-form answer, which is the
    behaviour being fixed.
    """
    service, conversation_id = _service(A_QUESTION)

    for typed in ("Full ledger", "2"):
        consumed = await maybe_resume_pending_interaction(
            _context(conversation_id), typed, conversation_service=service
        )
        assert consumed is True, typed


@pytest.mark.asyncio
async def test_typing_a_decision_still_answers_an_approval() -> None:
    """ "approve" and "deny" are answers wherever they are typed.

    Both directions, because only approve-words were ever recognised — anything
    else was read as a denial, which is what let an unrelated instruction deny a
    pending action silently.
    """
    for typed in ("approve", "deny"):
        service, conversation_id = _service(AN_APPROVAL)
        consumed = await maybe_resume_pending_interaction(
            _context(conversation_id), typed, conversation_service=service
        )
        assert consumed is True, typed


@pytest.mark.asyncio
async def test_a_card_that_arrived_as_text_is_answered_by_typing() -> None:
    """Nothing to tap, so a typed reply has to be read as the answer.

    This is the text fallback, and it is the reason the resume path exists.
    Recorded when the text is sent rather than inferred from the platform:
    Slack renders buttons and still falls back when a block payload is
    rejected, and somebody looking at plain text has nothing to tap.

    Free text on purpose — an offered label would pass on the plain-answer
    rule instead, and prove nothing about this one.
    """
    service, conversation_id = _service(
        A_QUESTION, free_text_wanted_for="call-the-question"
    )

    consumed = await maybe_resume_pending_interaction(
        _context(conversation_id),
        "whichever you think is best",
        conversation_service=service,
    )

    assert consumed is True
