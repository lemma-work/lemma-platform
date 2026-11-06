"""The clock that ends an Agent Host turn parked too long on a person.

The database half -- which pause is oldest, the mark, the cancel -- is driven
end to end in ``test_agent_host_parked_window_e2e``. What is pure is here: when
a pause counts as too old, when a run looks, and what the mark reads back as.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.agent.agent_host.domain.permissions import (
    AgentHostPermissionOption,
    AgentHostPermissionRequest,
)
from app.modules.agent.domain.run_state import ParkedSuspension
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.agent_host.harness.parked_window import (
    ParkedPause,
    ParkedWindow,
    parked_too_long,
)
from app.modules.agent.services.approval_reconciliation import (
    _permission_decision_message,
)

_ASKED = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _pause(waited: timedelta) -> ParkedPause:
    return ParkedPause(
        tool_call_id="lemma-mcp-1",
        tool_name="ask_user",
        asked_at=_ASKED,
        now=_ASKED + waited,
    )


@pytest.mark.parametrize(
    ("waited", "expected"),
    [
        (timedelta(minutes=9, seconds=59), False),
        (timedelta(minutes=10), True),
        (timedelta(hours=3), True),
    ],
)
def test_a_pause_is_too_old_from_the_window_on(waited, expected) -> None:
    assert parked_too_long(_pause(waited), window_seconds=600) is expected


def test_a_window_of_zero_never_ends_a_turn() -> None:
    assert parked_too_long(_pause(timedelta(days=1)), window_seconds=0) is False


def test_a_run_looks_once_per_interval() -> None:
    window = ParkedWindow(window_seconds=600, interval=30, next_check_at=100)

    assert window.due(99.9) is False
    assert window.due(100) is True
    # Armed for the next interval, not the next tick.
    assert window.due(101) is False
    assert window.due(130) is True


def test_a_run_stops_looking_once_its_turn_is_ending() -> None:
    window = ParkedWindow(window_seconds=600, interval=30, suspended=True)

    assert window.due(1_000_000) is False


def test_a_disabled_window_never_looks() -> None:
    window = ParkedWindow(window_seconds=0, interval=30)

    assert window.due(1_000_000) is False


def test_the_mark_reads_back_as_the_pause_it_names() -> None:
    stored = {
        "tool_call_id": "agent-host-permission:native-shell",
        "tool_name": "request_approval",
    }

    assert ParkedSuspension.from_json(stored) == ParkedSuspension(
        tool_call_id="agent-host-permission:native-shell",
        tool_name="request_approval",
    )


@pytest.mark.parametrize("stored", [None, {}, True, {"tool_call_id": "x"}])
def test_anything_else_is_not_a_mark(stored) -> None:
    assert ParkedSuspension.from_json(stored) is None


_REQUEST = AgentHostPermissionRequest(
    request_id="native-shell",
    options=(AgentHostPermissionOption(option_id="once", kind="allowonce"),),
)


def test_an_approval_after_the_turn_ended_says_to_do_it_again() -> None:
    message = _permission_decision_message(
        _REQUEST,
        decision=AgentRunApprovalDecision.APPROVE_ONCE,
        delivered=False,
        suspended=True,
    )

    assert message.startswith("Approved.")
    assert "nothing ran" in message and "do it again" in message


def test_an_approval_the_resume_carries_says_it_will_be_allowed() -> None:
    """A host that honours pre-approvals answers the repeat itself, so the
    agent is told it will not wait on the person again -- and only for an
    approval: a denial carried nowhere still says not to."""
    message = _permission_decision_message(
        _REQUEST,
        decision=AgentRunApprovalDecision.APPROVE_ONCE,
        delivered=False,
        suspended=True,
        carried=True,
    )

    assert message.startswith("Approved.")
    assert "allowed once without asking the person again" in message
    assert "asked to allow it once more" not in message


def test_a_denial_after_the_turn_ended_says_not_to() -> None:
    message = _permission_decision_message(
        _REQUEST,
        decision=AgentRunApprovalDecision.DENY,
        delivered=False,
        suspended=True,
    )

    assert message.startswith("Denied.")
    assert "Do not do it." in message
