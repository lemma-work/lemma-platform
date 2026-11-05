"""A pause left open by a finished run is denied on the person's behalf.

Before a new message starts a run, `SupersedeStalePauses` denies every pause a
person still had to decide: they moved on without answering, and a history
that still shows an open question makes the next run ask it again. A pause
somebody decided in the meantime is theirs: the fenced write loses, and its own
resolution writes the return.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.application.pauses.supersede import SupersedeStalePauses
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import PauseKind, PauseStatus
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision

pytestmark = pytest.mark.unit


def _open_question() -> Pause:
    return Pause(
        conversation_id=uuid4(),
        run_id=uuid4(),
        pod_id=uuid4(),
        tool_call_id="call-1",
        tool_name="ask_user",
        kind=PauseKind.QUESTION,
    )


def _coordinator(pause: Pause, *, wins: bool):
    pauses = AsyncMock()
    pauses.pending.return_value = [pause]
    pauses.save.return_value = wins
    resume_returns = AsyncMock()
    resume_returns.build.return_value = ("ask_user", {"denied": True})
    coordinator = SupersedeStalePauses(AsyncMock(), resume_returns=resume_returns)
    coordinator.pauses, coordinator.messages = pauses, AsyncMock()
    # No wait_for to cancel here; that half is in test_wait_for and the e2e.
    coordinator._cancel_superseded_wait = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return coordinator


@pytest.mark.asyncio
async def test_an_open_pause_is_denied_and_given_its_return() -> None:
    pause = _open_question()
    coordinator = _coordinator(pause, wins=True)

    written = await coordinator.supersede(
        conversation=SimpleNamespace(id=pause.conversation_id), user_id=uuid4()
    )

    assert len(written) == 1
    assert pause.status is PauseStatus.SUPERSEDED
    assert pause.decision is AgentRunApprovalDecision.DENY
    coordinator.messages.append_message.assert_awaited_once()
    assert coordinator.messages.append_message.await_args.kwargs["agent_run_id"] == (
        pause.run_id
    )


@pytest.mark.asyncio
async def test_a_pause_somebody_decided_first_is_left_to_them() -> None:
    pause = _open_question()
    coordinator = _coordinator(pause, wins=False)

    written = await coordinator.supersede(
        conversation=SimpleNamespace(id=pause.conversation_id), user_id=uuid4()
    )

    assert written == []
    coordinator.resume_returns.build.assert_not_awaited()
    coordinator.messages.append_message.assert_not_awaited()
