"""A pause left open by a finished run is denied, or else it is reported.

Before a new message starts a run, `supersede_stale_pending_interactions`
auto-denies any `ask_user`/`request_approval` still unanswered from an earlier
pause: the person moved on without answering, and a history that still shows an
open question makes the next run ask it again.

Rebuilding that denial needs three things off the original call, and the
caller's filter only proves two of them -- the tool call id and the tool name.
The run id it does not, and a call without one is skipped. That skip is correct
(a return synthesized against no run cannot be replayed into one) and it is
also invisible: the pause simply stays open, and the next run inherits it. So
it warns, and this file holds it to that.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.services.conversation_approvals import ApprovalCoordinator

pytestmark = pytest.mark.unit


def _pausing_call(*, agent_run_id):
    return SimpleNamespace(
        id=uuid4(),
        kind=MessageKind.TOOL_CALL,
        tool_name="ask_user",
        tool_call_id="call-1",
        tool_args={},
        agent_run_id=agent_run_id,
    )


def _coordinator(message):
    repository = AsyncMock()
    repository.pausing_calls_awaiting_a_decision.return_value = [message]
    repository.get_tool_return.return_value = None
    repository.record_approval_decision.return_value = True
    resume_returns = AsyncMock()
    resume_returns.build.return_value = ("ask_user", {"denied": True})
    coordinator = ApprovalCoordinator(
        AsyncMock(), repository, resume_returns, AsyncMock()
    )
    return coordinator, repository


@pytest.mark.asyncio
async def test_a_pausing_call_with_no_run_is_skipped_and_said_out_loud(
    caplog,
) -> None:
    conversation = SimpleNamespace(id=uuid4())
    coordinator, repository = _coordinator(_pausing_call(agent_run_id=None))

    with caplog.at_level("WARNING"):
        synthesized = await coordinator.supersede_stale_pending_interactions(
            conversation=conversation,
            user_id=uuid4(),
        )

    assert synthesized == []
    repository.record_approval_decision.assert_not_awaited()
    assert "pause_without_run_skipped" in caplog.text
    assert "call-1" in caplog.text, (
        "the warning has to name the call, or it says only that something "
        "somewhere was dropped"
    )


@pytest.mark.asyncio
async def test_a_pausing_call_with_a_run_is_denied_rather_than_skipped(
    caplog,
) -> None:
    """The ordinary path, so the guard above is not silently swallowing it."""
    conversation = SimpleNamespace(id=uuid4())
    coordinator, repository = _coordinator(_pausing_call(agent_run_id=uuid4()))

    with caplog.at_level("WARNING"):
        await coordinator.supersede_stale_pending_interactions(
            conversation=conversation,
            user_id=uuid4(),
        )

    repository.record_approval_decision.assert_awaited_once()
    assert "pause_without_run_skipped" not in caplog.text
