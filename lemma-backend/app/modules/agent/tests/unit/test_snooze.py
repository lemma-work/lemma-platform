"""Unit tests for agent snooze: the wait lifecycle and the tool's guards."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitStatus,
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.tools.snooze.models import (
    MAX_SNOOZE_SECONDS,
    MIN_SNOOZE_SECONDS,
    SnoozeRequest,
)
from app.modules.agent.tools.snooze.pydantic_adapter import snooze


def _wait(**overrides) -> AgentConversationWaitEntity:
    defaults = dict(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        pod_id=uuid4(),
        tool_call_id="tc-1",
    )
    return AgentConversationWaitEntity(**{**defaults, **overrides})


def _ctx(*, supports_pause_signal: bool = True, tool_call_id: str = "tc-1"):
    return SimpleNamespace(
        deps=SimpleNamespace(
            conversation_id=uuid4(),
            agent_run_id=uuid4(),
            pod_id=uuid4(),
            supports_pause_signal=supports_pause_signal,
        ),
        tool_call_id=tool_call_id,
    )


# -- wait lifecycle ------------------------------------------------------------


def test_waits_are_time_only():
    """Record waits were cut deliberately — a row changing is a trigger's job."""
    assert [member.value for member in AgentWaitType] == ["TIME"]


def test_complete_records_the_reason():
    wait = _wait()
    wait.complete(AgentWaitWakeReason.TIMER)
    assert wait.status is AgentWaitStatus.COMPLETED
    assert wait.spec["woke_because"] == AgentWaitWakeReason.TIMER.value
    assert wait.completed_at is not None


def test_cancel_is_distinguishable_from_a_normal_wake():
    wait = _wait()
    wait.cancel()
    assert wait.status is AgentWaitStatus.CANCELLED
    assert wait.spec["woke_because"] == AgentWaitWakeReason.CANCELLED.value


def test_completing_preserves_the_original_spec():
    wait = _wait(spec={"reason": "waiting for the build", "note_to_self": "post it"})
    wait.complete(AgentWaitWakeReason.TIMER)
    assert wait.spec["reason"] == "waiting for the build"
    assert wait.spec["note_to_self"] == "post it"


# -- request validation --------------------------------------------------------


def test_seconds_is_required():
    with pytest.raises(ValueError):
        SnoozeRequest(reason="waiting")


# -- the tool's guards ---------------------------------------------------------


@pytest.mark.asyncio
async def test_snooze_falls_back_when_the_runtime_cannot_pause():
    """Remote harnesses own their session; guide the model instead of hanging."""
    response = await snooze(
        _ctx(supports_pause_signal=False),
        SnoozeRequest(reason="waiting", seconds=600),
    )
    assert response.success is False
    assert response.interaction_fallback is True
    assert "end your turn" in (response.message or "")


@pytest.mark.asyncio
async def test_snooze_refuses_a_pointless_short_sleep():
    """Rejected, not clamped — a 5s ask means the model misread the tool."""
    response = await snooze(
        _ctx(), SnoozeRequest(reason="waiting", seconds=MIN_SNOOZE_SECONDS - 1)
    )
    assert response.success is False
    assert "Minimum snooze" in (response.error or "")


@pytest.mark.asyncio
async def test_snooze_requires_an_active_run():
    ctx = _ctx()
    ctx.deps.agent_run_id = None
    response = await snooze(ctx, SnoozeRequest(reason="waiting", seconds=600))
    assert response.success is False
    assert "active agent run" in (response.error or "")


@pytest.mark.asyncio
async def test_snooze_requires_a_pod():
    ctx = _ctx()
    ctx.deps.pod_id = None
    response = await snooze(ctx, SnoozeRequest(reason="waiting", seconds=600))
    assert response.success is False
    assert "inside a pod" in (response.error or "")


@pytest.mark.asyncio
async def test_snooze_requires_a_durable_tool_call_id():
    response = await snooze(
        _ctx(tool_call_id=""), SnoozeRequest(reason="waiting", seconds=600)
    )
    assert response.success is False
    assert "durable tool call id" in (response.error or "")


def test_ceiling_is_a_day():
    assert MAX_SNOOZE_SECONDS == 24 * 60 * 60
