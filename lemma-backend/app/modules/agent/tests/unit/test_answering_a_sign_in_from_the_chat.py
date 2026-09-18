"""Answering `browser_sign_in` through the ordinary approval decision.

The card in the conversation resolves a waiting sign-in the same way it
resolves an `ask_user`, because that is the path the transcript subscribes to.
Answering anywhere else -- as the standalone page's own endpoint once did --
resolved the pause server-side while the screen the person was looking at
never heard about it, so the card kept asking over a run that had already
carried on.

This file used to be twice the size, and the half that went was the capture:
reading the browser at this point, deciding which cookies were the login, and
storing them. That ran *after* the execution claim had been committed, so
anything it raised wrote no tool return at all -- and a paused call with no
return is a conversation nobody can get out of, because the composer is locked
on the pause and only a message can supersede it. The browser keeps its own
profile now. There is nothing to capture here, so there is nothing here to
fail, and the tests for that hazard are gone with the hazard.

What is left is what the agent gets told.
"""

from __future__ import annotations

from uuid import uuid4, uuid7

import pytest

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.conversation_resume_return import (
    ResumeToolReturnBuilder,
)

pytestmark = pytest.mark.unit

_ORIGIN = "https://asur.work"


class _Uow:
    def after_commit(self, _callback) -> None:
        return None


def _builder() -> ResumeToolReturnBuilder:
    return ResumeToolReturnBuilder(_Uow(), agent_repository=None)  # type: ignore[arg-type]


async def _answer(
    decision: AgentRunApprovalDecision, response: dict | None = None
) -> dict:
    return await _builder()._browser_sign_in_return(
        tool_args={"origin": _ORIGIN},
        decision=decision,
        response=response or {},
        user_id=uuid4(),
        conversation_id=uuid7(),
    )


async def test_signing_in_tells_the_agent_to_carry_on() -> None:
    result = await _answer(AgentRunApprovalDecision.APPROVE_ONCE)

    assert result["outcome"] == "signed_in"
    assert result["source"] == "person"
    assert result["origin"] == _ORIGIN
    assert "carry on" in result["message"]


async def test_a_site_still_showing_a_form_is_passed_on_as_a_warning() -> None:
    """The standalone page looks at the site straight after the person
    finishes and puts the answer on the decision. It is a warning rather than
    a failure: the check is a heuristic, and the person has already done what
    was asked."""
    result = await _answer(
        AgentRunApprovalDecision.APPROVE_ONCE, {"working": False}
    )

    assert result["outcome"] == "signed_in"
    assert "still showed a login form" in result["message"]


async def test_a_card_answered_in_the_chat_does_not_claim_a_check_it_never_made() -> (
    None
):
    """The card has no browser to ask, so `working` is simply absent.

    Absent must not read as "not working" -- that would tell the agent the
    site rejected a sign-in nobody looked at.
    """
    result = await _answer(AgentRunApprovalDecision.APPROVE_ONCE)

    assert "still showed a login form" not in result["message"]


async def test_declining_says_so_and_tells_the_agent_not_to_re_ask() -> None:
    result = await _answer(AgentRunApprovalDecision.DENY)

    assert result["outcome"] == "declined"
    assert "Do not ask again" in result["message"]


def test_nothing_on_this_path_can_read_or_store_a_session() -> None:
    """Structural. The deadlock this used to risk came from doing browser
    work after the execution claim was committed; the guarantee that it
    cannot happen again is that there is no such work left to do."""
    for gone in ("_capture_for_resume", "_sign_in_service"):
        assert not hasattr(ResumeToolReturnBuilder, gone), f"{gone} should be gone"
