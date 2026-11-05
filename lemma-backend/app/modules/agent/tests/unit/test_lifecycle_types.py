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
from app.modules.agent.domain.pauses import (
    CALL_NAMES,
    PAUSING_CALL_NAMES,
    PERSON_KINDS,
)
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
)
from app.modules.agent.application.conversations.read import ConversationReads

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


def test_every_pause_kind_names_the_call_it_is_stored_under() -> None:
    assert set(CALL_NAMES) == set(PauseKind)
    assert set(CALL_NAMES.values()) == PAUSING_CALL_NAMES
    assert PauseKind.WAIT not in PERSON_KINDS


def test_approval_resolution_statuses_keep_the_api_spelling() -> None:
    assert [status.value for status in ApprovalResolutionStatus] == [
        "resolved",
        "reconciled",
        "queued",
    ]


@pytest.mark.asyncio
async def test_the_oldest_unresolved_pause_is_read_once_for_every_kind() -> None:
    asked: list[dict[str, object]] = []
    pause = object()

    async def _pending(conversation_id, **kwargs):
        asked.append(kwargs)
        return [pause]

    reads = ConversationReads.__new__(ConversationReads)
    reads.pauses = SimpleNamespace(pending=_pending)

    assert await reads.pending_user_interaction(conversation_id=uuid4()) is pause
    assert asked == [{"kinds": frozenset(PauseKind), "limit": 1}]
