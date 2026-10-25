"""The lifecycle's states are types, and the maps between them are total."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.approvals import ApprovalResolutionStatus
from app.modules.agent.domain.conversation_lifecycle import (
    ACTIVE_CONVERSATION_STATUSES,
    TERMINAL_CONVERSATION_STATUSES,
    conversation_status_for,
)
from app.modules.agent.domain.pausing_tools import (
    PAUSING_TOOL_NAMES,
    USER_PAUSING_TOOL_NAMES,
    PausingKind,
    PendingPause,
)
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.services.conversation_approvals import ApprovalCoordinator

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("run_status", list(AgentRunStatus))
def test_every_run_status_says_what_its_conversation_shows(
    run_status: AgentRunStatus,
) -> None:
    assert isinstance(conversation_status_for(run_status), ConversationStatus)


def test_the_conversation_sets_are_the_run_sets() -> None:
    assert {s.value for s in ACTIVE_CONVERSATION_STATUSES} == {
        s.value for s in ACTIVE_AGENT_RUN_STATUSES
    }
    assert {s.value for s in TERMINAL_CONVERSATION_STATUSES} == {
        s.value for s in TERMINAL_AGENT_RUN_STATUSES
    }
    # A pause is a resting state: neither going nor finished.
    assert ConversationStatus.WAITING not in ACTIVE_CONVERSATION_STATUSES
    assert ConversationStatus.WAITING not in TERMINAL_CONVERSATION_STATUSES


@pytest.mark.parametrize("enum", [AgentRunStatus, ConversationStatus, MessageKind])
def test_stored_statuses_have_one_spelling(enum: type) -> None:
    """Every stored status is capitals now, so a lowercase one is an error to
    surface, not a spelling to accept."""
    member = next(iter(enum))
    with pytest.raises(ValueError):
        enum(member.value.lower())


def test_pausing_kinds_are_the_pausing_tool_names() -> None:
    assert {kind.value for kind in PausingKind} == set(PAUSING_TOOL_NAMES)
    assert PausingKind.WAIT_FOR.value not in USER_PAUSING_TOOL_NAMES


def test_approval_resolution_statuses_keep_the_api_spelling() -> None:
    assert [status.value for status in ApprovalResolutionStatus] == [
        "resolved",
        "reconciled",
        "queued",
    ]


@pytest.mark.asyncio
async def test_the_oldest_unresolved_pause_is_a_typed_pending_pause() -> None:
    run_id = uuid4()

    async def _awaiting(**_):
        return [
            SimpleNamespace(
                tool_call_id="call-1",
                tool_name="request_approval",
                tool_args={"action": "deploy"},
                agent_run_id=run_id,
            )
        ]

    coordinator = ApprovalCoordinator.__new__(ApprovalCoordinator)
    coordinator.pausing_calls = SimpleNamespace(
        pausing_calls_awaiting_a_decision=_awaiting
    )

    pause = await coordinator.oldest_unresolved_pause(
        conversation_id=uuid4(), tool_names=PAUSING_TOOL_NAMES
    )

    assert pause == PendingPause(
        tool_call_id="call-1",
        kind=PausingKind.REQUEST_APPROVAL,
        tool_args={"action": "deploy"},
        agent_run_id=run_id,
    )
