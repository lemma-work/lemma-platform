"""A pause ends once; a second ending changes nothing.

A Stop that lands just after the wake it raced used to overwrite the finished
wait: COMPLETED became CANCELLED, ``woke_because`` and ``completed_at`` were
rewritten, and the row no longer said what the history it wrote did. A person's
pause is the same: an expiry that lands after the answer must not turn it into
a DENY.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.modules.agent.domain.approvals import ANSWERED_BY_REPLY
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import (
    AgentRunApprovalDecision,
    PauseKind,
    PauseStatus,
)
from app.modules.agent.domain.wait import AgentWaitType, AgentWaitWakeReason, WaitSpec

pytestmark = pytest.mark.unit

Ending = Callable[[Pause], bool]

_OWNER = uuid4()
_ENDINGS: dict[str, Ending] = {
    "wake": lambda pause: pause.wake(AgentWaitWakeReason.TIMER),
    "cancel": lambda pause: pause.cancel(),
    "abandon": lambda pause: pause.abandon("wake failed"),
    "resolve": lambda pause: pause.resolve(
        AgentRunApprovalDecision.APPROVE_ONCE, {"note": "ok"}, by=_OWNER
    ),
    "expire": lambda pause: pause.expire(datetime.now(UTC), on_behalf_of=_OWNER),
    "supersede": lambda pause: pause.supersede(by=_OWNER),
}


def _pause(kind: PauseKind = PauseKind.WAIT) -> Pause:
    return Pause(
        conversation_id=uuid4(),
        run_id=uuid4(),
        pod_id=uuid4(),
        tool_call_id="tc-1",
        tool_name="wait_for",
        kind=kind,
        wait_type=AgentWaitType.TIME if kind is PauseKind.WAIT else None,
        wait_spec=WaitSpec() if kind is PauseKind.WAIT else None,
    )


@pytest.mark.parametrize("first", list(_ENDINGS))
@pytest.mark.parametrize("second", list(_ENDINGS))
def test_a_finished_pause_keeps_its_first_ending(first: str, second: str) -> None:
    pause = _pause()
    assert _ENDINGS[first](pause) is True
    ended = pause.model_dump()

    assert _ENDINGS[second](pause) is False
    assert pause.model_dump() == ended


def test_each_wait_ending_records_why() -> None:
    woken, cancelled, abandoned = _pause(), _pause(), _pause()

    woken.wake(AgentWaitWakeReason.TIMER)
    cancelled.cancel()
    abandoned.abandon("wake failed")

    assert woken.status is PauseStatus.RESOLVED
    assert woken.wait_spec and woken.wait_spec.woke_because is AgentWaitWakeReason.TIMER
    assert cancelled.status is PauseStatus.CANCELLED
    assert cancelled.wait_spec and (
        cancelled.wait_spec.woke_because is AgentWaitWakeReason.CANCELLED
    )
    assert abandoned.status is PauseStatus.ABANDONED
    assert (
        abandoned.wait_spec and abandoned.wait_spec.abandoned_because == "wake failed"
    )


def test_nobody_deciding_is_a_deny_that_says_why() -> None:
    expired, superseded = _pause(PauseKind.QUESTION), _pause(PauseKind.APPROVAL)

    expired.expire(datetime.now(UTC), on_behalf_of=_OWNER)
    superseded.supersede(by=_OWNER)

    assert expired.status is PauseStatus.EXPIRED
    assert expired.decision is AgentRunApprovalDecision.DENY
    assert expired.response == {"expired_unanswered": True}
    assert superseded.status is PauseStatus.SUPERSEDED
    assert superseded.decision is AgentRunApprovalDecision.DENY
    assert superseded.response == {"superseded_by_new_message": True}


def test_only_a_decided_approval_may_run_its_tool_and_only_once() -> None:
    question, denied, approved = (
        _pause(PauseKind.QUESTION),
        _pause(PauseKind.APPROVAL),
        _pause(PauseKind.APPROVAL),
    )
    question.resolve(AgentRunApprovalDecision.APPROVE_ONCE, by=_OWNER)
    denied.resolve(AgentRunApprovalDecision.DENY, by=_OWNER)
    approved.resolve(AgentRunApprovalDecision.APPROVE_ONCE, by=_OWNER)

    assert question.claim_execution() is False
    assert denied.claim_execution() is False
    assert approved.claim_execution() is True
    assert approved.claim_execution() is False


def test_a_question_written_past_keeps_the_reply_for_the_model() -> None:
    """Live, a free-text reply to an open question reached the model as the
    question "dismissed". The question is still passed over -- nobody answered
    it -- but the reply is kept for the model as its answer. An approval keeps
    nothing: a message that does not say which way never decides one."""
    question, approval = _pause(PauseKind.QUESTION), _pause(PauseKind.APPROVAL)

    assert question.supersede(by=uuid4(), reply="The 3rd to the 5th.")
    assert approval.supersede(by=uuid4(), reply="yes")

    assert question.status is PauseStatus.SUPERSEDED
    assert question.response[ANSWERED_BY_REPLY] == "The 3rd to the 5th."
    assert ANSWERED_BY_REPLY not in approval.response
