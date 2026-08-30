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
    ResumeOutcome,
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


# One question with two offered options. `_is_an_answer` (#575) only lets a
# typed message through when it plainly answers -- an offered label, its index,
# or a decision word -- so a test about *recording* an answer has to supply one
# that qualifies, or it never reaches the write it is about.
_ONE_QUESTION = {
    "questions": [
        {
            "header": "colour",
            "question": "Which colour?",
            "options": [{"label": "Red"}, {"label": "Blue"}],
            "multiSelect": False,
        }
    ]
}


async def _resume(
    text: str,
    *,
    kind: str = "request_approval",
    tool_args: dict | None = None,
    resolve_raises: Exception | None = None,
    lookup_raises: Exception | None = None,
):
    """Run the resume path against a stub conversation service.

    Returns ``(outcome, resolve_mock)`` so a test can assert both halves: what
    the caller is told to do, and whether a decision was recorded.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from app.modules.agent_surfaces.services.pending_interaction_resume import (
        maybe_resume_pending_interaction,
    )

    conversation = SimpleNamespace(user_id=uuid4(), pod_id=uuid4())
    resolve = AsyncMock(side_effect=resolve_raises)
    service = SimpleNamespace(
        get_pending_user_interaction=AsyncMock(
            return_value={
                "tool_call_id": "tool-1",
                "kind": kind,
                "tool_args": tool_args if tool_args is not None else {},
            },
            side_effect=lookup_raises,
        ),
        conversation_repository=SimpleNamespace(
            get_conversation=AsyncMock(return_value=conversation),
            # `_is_an_answer` asks this when the words do not plainly answer.
            get_conversation_metadata_key=AsyncMock(return_value=None),
        ),
        resolve_user_approval_internal=resolve,
    )
    context = SimpleNamespace(
        conversation_id=uuid4(),
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )
    outcome = await maybe_resume_pending_interaction(
        context, text, conversation_service=service
    )
    return outcome, resolve


async def test_a_decision_is_consumed_and_recorded() -> None:
    outcome, resolve = await _resume("approve")
    assert outcome is ResumeOutcome.CONSUMED
    assert (
        resolve.await_args.kwargs["decision"] is AgentRunApprovalDecision.APPROVE_ONCE
    )


async def test_a_non_decision_is_not_consumed_and_records_nothing() -> None:
    """The message falls through to become a real message, as the person meant.

    Both halves matter. Recording nothing leaves the pause for the new turn to
    supersede with an explicit denial; NOT_A_DECISION is what lets the words
    reach the agent at all.
    """
    outcome, resolve = await _resume("wait, why do you need that?")
    assert outcome is ResumeOutcome.NOT_A_DECISION
    resolve.assert_not_awaited()


async def test_an_offered_option_typed_out_answers_the_question() -> None:
    """A person with buttons in front of them may still type the option's words.

    Free text no longer answers on its own -- #575 made a message typed past a
    card a message rather than the answer to it -- so what this pins is the
    other half: something that plainly answers still resolves the pause.
    """
    outcome, resolve = await _resume("Red", kind="ask_user", tool_args=_ONE_QUESTION)
    assert outcome is ResumeOutcome.CONSUMED
    assert (
        resolve.await_args.kwargs["decision"] is AgentRunApprovalDecision.APPROVE_ONCE
    )


# --- when writing the decision down fails ----------------------------------


async def test_a_decision_we_could_not_record_is_never_a_denial() -> None:
    """The whole reason this returns three things instead of two.

    ``FAILED`` and ``NOT_A_DECISION`` both used to be ``False``, and the caller
    reads ``False`` as "deliver it as a message" — which starts a turn, and
    starting a turn supersedes the pause with an auto-DENY. So a database hiccup
    while recording an "approve" cancelled the action the person had just
    approved, and said so only at debug level.
    """
    outcome, _ = await _resume("approve", resolve_raises=RuntimeError("db is down"))
    assert outcome is ResumeOutcome.FAILED
    assert outcome is not ResumeOutcome.NOT_A_DECISION


async def test_an_ask_user_answer_we_could_not_record_fails_the_same_way() -> None:
    """Not approval-specific: a lost answer leaves the same run WAITING."""
    outcome, _ = await _resume(
        "Red",
        kind="ask_user",
        tool_args=_ONE_QUESTION,
        resolve_raises=RuntimeError("db is down"),
    )
    assert outcome is ResumeOutcome.FAILED


async def test_failing_to_find_the_pause_is_not_a_failed_decision() -> None:
    """We never learned there was one, so there is no decision to lose.

    Treating this as FAILED would answer every ordinary message with an
    apology. The turn the caller starts will fail on the same broken session
    anyway, so falling through is both safe and honest.
    """
    outcome, resolve = await _resume(
        "hello there", lookup_raises=RuntimeError("db is down")
    )
    assert outcome is ResumeOutcome.NOT_A_DECISION
    resolve.assert_not_awaited()


def test_the_failure_is_logged_where_production_can_see_it() -> None:
    """``LOG_LEVEL=INFO`` drops debug, which is where this used to be logged."""
    from app.core.log.event_catalog import EVENT_CATALOG

    assert (
        EVENT_CATALOG[
            "agent_surfaces.ingress_service.typed_reply_decision_not_recorded.failed"
        ].level
        == "error"
    )
    assert (
        EVENT_CATALOG[
            "agent_surfaces.ingress_service.typed_reply_lookup_failed.degraded"
        ].level
        == "warning"
    )
