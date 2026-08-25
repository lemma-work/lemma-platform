"""Classifying a typed approval reply, and the third case that matters.

A reply to a pending ``request_approval`` is a decision, or it is not. The
"not" case is the one these tests exist for: it used to be folded into DENY,
which cancelled the action *and* discarded what the person wrote.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent_surfaces.domain.models import (
    APPROVAL_DECISION_APPROVE,
    APPROVAL_DECISION_DENY,
    APPROVAL_DECISION_SESSION,
    SurfaceApprovalButton,
    SurfaceApprovalRenderPlan,
)
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    _classify_approval_reply,
)


@pytest.mark.parametrize(
    "text",
    [
        "approve",
        "Approve",
        "yes",
        "Yes!",
        "y",
        "ok",
        "sure",
        "go ahead",
        "yeah go ahead",
        "do it",
        "go for it",
        "lgtm",
        "sounds good",
        "1",
        "👍",
    ],
)
def test_approval_words_approve_once(text: str) -> None:
    assert _classify_approval_reply(text) is AgentRunApprovalDecision.APPROVE_ONCE


@pytest.mark.parametrize(
    "text",
    [
        "approve session",
        "approve for session",
        "always allow",
        "yes to all",
        "dont ask again",
        "don't ask again",
        "don’t ask again",
    ],
)
def test_session_words_approve_for_session(text: str) -> None:
    """The decision the text fallback used to drop entirely."""
    assert (
        _classify_approval_reply(text) is AgentRunApprovalDecision.APPROVE_FOR_SESSION
    )


@pytest.mark.parametrize(
    "text",
    ["deny", "no", "n", "nope", "cancel", "stop", "don't", "never mind", "2", "👎"],
)
def test_denial_words_deny(text: str) -> None:
    assert _classify_approval_reply(text) is AgentRunApprovalDecision.DENY


@pytest.mark.parametrize(
    "text",
    [
        "wait, why do you need that?",
        "yes, but only if it's the staging table",
        "actually delete the other one instead",
        "what does that command do?",
        "maybe later",
        "hold on",
        "",
        "   ",
    ],
)
def test_everything_else_is_not_a_decision(text: str) -> None:
    """None, so the caller delivers the message instead of inventing a decision.

    Each of these used to be DENY: the action was cancelled and the words were
    thrown away, including the two that are questions about the action itself.
    """
    assert _classify_approval_reply(text) is None


def test_a_qualified_yes_is_not_consent() -> None:
    """The reason the sets are exact matches rather than prefixes."""
    assert _classify_approval_reply("yes, but only if X") is None


def _plan(*decisions: str) -> SurfaceApprovalRenderPlan:
    return SurfaceApprovalRenderPlan(
        title="Delete orders",
        callback_id="conversation|tool-call",
        buttons=[
            SurfaceApprovalButton(label=decision, decision=decision)
            for decision in decisions
        ],
    )


def test_text_fallback_names_only_the_choices_the_card_has() -> None:
    text = _plan(APPROVAL_DECISION_APPROVE, APPROVAL_DECISION_DENY).to_plain_text()
    assert '"approve"' in text
    assert '"deny"' in text
    assert "session" not in text


def test_text_fallback_offers_session_when_the_card_does() -> None:
    """Present natively since day one; missing from the text prompt until now."""
    text = _plan(
        APPROVAL_DECISION_APPROVE,
        APPROVAL_DECISION_DENY,
        APPROVAL_DECISION_SESSION,
    ).to_plain_text()
    assert '"approve session"' in text


@pytest.mark.parametrize(
    "decisions",
    [
        (APPROVAL_DECISION_APPROVE, APPROVAL_DECISION_DENY),
        (
            APPROVAL_DECISION_APPROVE,
            APPROVAL_DECISION_DENY,
            APPROVAL_DECISION_SESSION,
        ),
    ],
)
def test_every_phrase_the_prompt_quotes_is_one_the_parser_accepts(
    decisions: tuple[str, ...],
) -> None:
    """The prompt and the parser have to agree, or the fallback is a dead end."""
    instruction = _plan(*decisions).reply_instruction()
    quoted = [part.split('"')[0] for part in instruction.split('"')[1::2]]
    assert quoted
    for phrase in quoted:
        assert _classify_approval_reply(phrase) is not None, phrase


async def _resume(text: str, *, kind: str = "request_approval"):
    """Run the resume path against a stub conversation service.

    Returns ``(consumed, resolve_mock)`` so a test can assert both halves: was
    the message eaten, and was a decision recorded.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.modules.agent_surfaces.services.pending_interaction_resume import (
        maybe_resume_pending_interaction,
    )

    conversation = SimpleNamespace(user_id=uuid4(), pod_id=uuid4())
    resolve = AsyncMock()
    service = SimpleNamespace(
        get_pending_user_interaction=AsyncMock(
            return_value={"tool_call_id": "tool-1", "kind": kind, "tool_args": {}}
        ),
        conversation_repository=SimpleNamespace(
            get_conversation=AsyncMock(return_value=conversation)
        ),
        resolve_user_approval_internal=resolve,
    )
    context = SimpleNamespace(
        conversation_id=uuid4(),
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )
    consumed = await maybe_resume_pending_interaction(
        context, text, conversation_service=service
    )
    return consumed, resolve


async def test_a_decision_is_consumed_and_recorded() -> None:
    consumed, resolve = await _resume("approve")
    assert consumed is True
    assert (
        resolve.await_args.kwargs["decision"] is AgentRunApprovalDecision.APPROVE_ONCE
    )


async def test_a_non_decision_is_not_consumed_and_records_nothing() -> None:
    """The message falls through to become a real message, as the person meant.

    Both halves matter. Recording nothing leaves the pause for the new turn to
    supersede with an explicit denial; returning False is what lets the words
    reach the agent at all.
    """
    consumed, resolve = await _resume("wait, why do you need that?")
    assert consumed is False
    resolve.assert_not_awaited()


async def test_ask_user_still_accepts_any_text_as_the_answer() -> None:
    """Unchanged: free text is a valid answer to a question, unlike an approval."""
    consumed, resolve = await _resume("the third one", kind="ask_user")
    assert consumed is True
    assert (
        resolve.await_args.kwargs["decision"] is AgentRunApprovalDecision.APPROVE_ONCE
    )
