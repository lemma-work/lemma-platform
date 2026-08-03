"""Unit tests for agent snooze: the wait lifecycle and the tool's guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.modules.agent.tools.snooze.pydantic_adapter as adapter
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
from app.modules.agent.tools.tool_errors import AgentInputRequired


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
            user_id=uuid4(),
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


# -- the suspend path ----------------------------------------------------------


@pytest.fixture
def suspend_harness(monkeypatch):
    """Stub the two side effects of a successful snooze: the timer and the row."""
    scheduled: list[dict] = []
    created: list[AgentConversationWaitEntity] = []

    async def _fake_schedule_snooze_wake(*, conversation_id, user_id, wake_at):
        timer_id = uuid4()
        scheduled.append(
            {
                "timer_id": timer_id,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "wake_at": wake_at,
            }
        )
        return timer_id

    class _FakeRepo:
        def __init__(self, uow):
            pass

        async def create(self, wait):
            created.append(wait)
            return wait

    class _FakeUow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            pass

    monkeypatch.setattr(adapter, "schedule_snooze_wake", _fake_schedule_snooze_wake)
    monkeypatch.setattr(adapter, "AgentConversationWaitRepository", _FakeRepo)
    monkeypatch.setattr(
        adapter, "SessionUnitOfWorkFactory", lambda maker: lambda: _FakeUow()
    )
    return SimpleNamespace(scheduled=scheduled, created=created)


@pytest.mark.asyncio
async def test_snooze_schedules_a_timer_and_pauses_the_run(suspend_harness):
    """The success path: one timer, one ACTIVE wait, and the pause signal.

    Guards the scheduler call shape. The composition adapter requires
    ``user_id``, and nothing else here would notice if the call drifted — the
    tool raises before returning, so a TypeError would surface only in
    production.
    """
    ctx = _ctx()
    with pytest.raises(AgentInputRequired) as raised:
        await snooze(ctx, SnoozeRequest(reason="waiting for the build", seconds=600))

    assert raised.value.tool_call_id == "tc-1"
    assert raised.value.kind == "snooze"

    (job,) = suspend_harness.scheduled
    assert job["user_id"] == ctx.deps.user_id
    assert job["conversation_id"] == ctx.deps.conversation_id
    # The wake path resolves the fired timer through wait_ref, so the token the
    # adapter returns must be what lands in external_ref.
    (wait,) = suspend_harness.created
    assert str(job["timer_id"]) == wait.external_ref
    assert wait.status is AgentWaitStatus.ACTIVE
    assert wait.tool_call_id == "tc-1"
    assert wait.spec["note_to_self"] is None


@pytest.mark.asyncio
async def test_snooze_clamps_an_over_long_request(suspend_harness):
    """The ceiling is policy, not a misunderstanding, so it clamps rather than errors."""
    ctx = _ctx()
    with pytest.raises(AgentInputRequired):
        await snooze(
            ctx, SnoozeRequest(reason="waiting", seconds=MAX_SNOOZE_SECONDS * 10)
        )

    (wait,) = suspend_harness.created
    slept = (
        wait.scheduled_at - datetime.fromisoformat(wait.spec["started_at"])
    ).total_seconds()
    assert slept == pytest.approx(MAX_SNOOZE_SECONDS, abs=1)
    # The unclamped ask is kept so the wake can see what the model actually wanted.
    assert wait.spec["requested_seconds"] == MAX_SNOOZE_SECONDS * 10


# -- the composition adapter ---------------------------------------------------


@pytest.mark.asyncio
async def test_snooze_wake_adapter_calls_the_scheduler_with_a_real_signature(monkeypatch):
    """The payload is the contract with ScheduleStartService.handle_schedule_fired.

    This is the one place the real ``schedule_once_job`` signature is exercised.
    It gained a required ``user_id`` when schedule-run ownership landed; a call
    that drifts from it raises TypeError only at runtime, because the tool that
    makes it raises AgentInputRequired rather than returning.
    """
    from app.composition import agent_snooze_scheduler as sched

    calls: list[dict] = []

    class _FakeClient:
        async def schedule_once_job(
            self,
            schedule_id,
            user_id,
            run_date,
            payload=None,
            replace_existing=True,
            logical_schedule=False,
        ):
            calls.append(
                {
                    "schedule_id": schedule_id,
                    "user_id": user_id,
                    "run_date": run_date,
                    "payload": payload,
                }
            )

    monkeypatch.setattr(sched, "SchedulerAPIClient", lambda: _FakeClient())

    conversation_id, user_id = uuid4(), uuid4()
    wake_at = datetime.now(timezone.utc) + timedelta(seconds=600)
    timer_id = await sched.schedule_snooze_wake(
        conversation_id=conversation_id, user_id=user_id, wake_at=wake_at
    )

    (call,) = calls
    assert call["schedule_id"] == timer_id
    assert call["user_id"] == user_id
    assert call["run_date"] == wake_at
    # conversation_id selects the snooze branch; wait_ref resolves the fired
    # timer to exactly one ACTIVE wait.
    assert call["payload"]["conversation_id"] == str(conversation_id)
    assert call["payload"]["wait_ref"] == str(timer_id)
    assert call["payload"]["source"] == "agent_snooze"
