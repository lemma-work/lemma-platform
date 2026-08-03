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


# -- the sweep: grace period, isolation, and the attempt cap --------------------


@pytest.fixture
def sweep(monkeypatch):
    """Drive SnoozeReconcileService with in-memory units of work."""
    from app.modules.agent.services import snooze_reconcile_service as svc

    state = SimpleNamespace(listed=[], attempts={}, abandoned=[], woke=[], cutoffs=[])

    class _FakeRepo:
        def __init__(self, uow):
            pass

        async def list_active_due(self, *, due_before, limit):
            state.cutoffs.append(due_before)
            return list(state.listed)

        async def record_wake_attempt(self, wait_id):
            state.attempts[wait_id] = state.attempts.get(wait_id, 0) + 1
            return state.attempts[wait_id]

        async def claim(self, wait_id):
            return next((w for w in state.listed if w.id == wait_id), None)

        async def update(self, wait):
            if wait.status is AgentWaitStatus.FAILED:
                state.abandoned.append(wait)
            return wait

    class _FakeUow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            pass

    monkeypatch.setattr(svc, "AgentConversationWaitRepository", _FakeRepo)
    monkeypatch.setattr(svc, "SessionUnitOfWorkFactory", lambda maker: lambda: _FakeUow())
    monkeypatch.setattr(svc, "async_session_maker", object())
    state.service = svc.SnoozeReconcileService()
    state.module = svc
    return state


@pytest.mark.asyncio
async def test_sweep_leaves_a_grace_period_before_calling_a_timer_lost(sweep):
    """Without it the 5-minute sweep races the real timer on every snooze.

    Every healthy wait would then be woken by the backstop and logged as a lost
    timer at WARNING — alert noise that hides the failure it exists to report.
    """
    before = datetime.now(timezone.utc)
    await sweep.service.reconcile_due_waits()
    after = datetime.now(timezone.utc)

    # The sweep asks for waits overdue by a full grace period, never merely due.
    (cutoff,) = sweep.cutoffs
    grace = sweep.module.RECONCILE_AFTER
    assert before - grace <= cutoff <= after - grace
    # And it matches the workflow sweep it claims to mirror.
    from app.modules.workflow.services.run_resume_service import RECONCILE_AFTER

    assert sweep.module.RECONCILE_AFTER == RECONCILE_AFTER


@pytest.mark.asyncio
async def test_sweep_abandons_a_wait_whose_wake_never_succeeds(sweep, monkeypatch):
    """The poison pill: without a cap this retries every 5 minutes forever."""

    class _AlwaysFails:
        def __init__(self, uow):
            pass

        async def wake(self, *, wait, reason):
            raise RuntimeError("wake is broken")

    monkeypatch.setattr(sweep.module, "SnoozeWakeService", _AlwaysFails)
    wait = _wait(scheduled_at=datetime.now(timezone.utc))
    sweep.listed = [wait]

    for _ in range(sweep.module.MAX_WAKE_ATTEMPTS):
        assert await sweep.service.reconcile_due_waits() == 0
        assert not sweep.abandoned  # still retrying

    await sweep.service.reconcile_due_waits()
    (abandoned,) = sweep.abandoned
    assert abandoned.status is AgentWaitStatus.FAILED
    assert "wake failed" in abandoned.spec["abandoned_because"]


@pytest.mark.asyncio
async def test_sweep_counts_an_attempt_before_making_it(sweep, monkeypatch):
    """The counter must survive the rollback of the wake it is counting.

    Bumped inside the failing transaction it would roll back with it, and the
    cap would never be reached.
    """
    order: list[str] = []

    class _Recording:
        def __init__(self, uow):
            pass

        async def wake(self, *, wait, reason):
            order.append("wake")
            raise RuntimeError("boom")

    monkeypatch.setattr(sweep.module, "SnoozeWakeService", _Recording)
    original = sweep.service._count_attempt

    async def _spy(wait):
        order.append("count")
        return await original(wait)

    monkeypatch.setattr(sweep.service, "_count_attempt", _spy)
    sweep.listed = [_wait(scheduled_at=datetime.now(timezone.utc))]

    await sweep.service.reconcile_due_waits()
    assert order == ["count", "wake"]


@pytest.mark.asyncio
async def test_one_bad_wait_does_not_stop_the_rest_of_the_batch(sweep, monkeypatch):
    """Each wait gets its own session, so a rollback is contained."""
    bad, good = _wait(), _wait()

    class _FailsTheFirst:
        def __init__(self, uow):
            pass

        async def wake(self, *, wait, reason):
            if wait.id == bad.id:
                raise RuntimeError("boom")
            sweep.woke.append(wait.id)
            return True

    monkeypatch.setattr(sweep.module, "SnoozeWakeService", _FailsTheFirst)
    sweep.listed = [bad, good]

    assert await sweep.service.reconcile_due_waits() == 1
    assert sweep.woke == [good.id]


# -- Stop, on a sleeping agent -------------------------------------------------


@pytest.mark.asyncio
async def test_stop_cancels_the_wait_drops_the_timer_and_does_not_resume(monkeypatch):
    """Stop on a snoozed conversation used to do nothing at all.

    ``stop_conversation`` only acted when there was an active run, and a snoozed
    turn has none by construction — the run ended cleanly when the tool paused
    it. So Stop returned success, the conversation stayed WAITING, and the timer
    still fired later and resumed the agent the user had just stopped.
    """
    from app.modules.agent.services import conversation_service as cs

    wait = _wait(external_ref=str(uuid4()), spec={"note_to_self": "post it"})
    removed: list[str] = []
    appended: list[dict] = []
    resumed: list[str] = []
    statuses: list[object] = []

    class _Waits:
        def __init__(self, uow):
            pass

        async def find_active_for_conversation(self, conversation_id):
            return wait

        async def update(self, updated):
            return updated

    class _Conversations:
        async def set_conversation_status(self, *, conversation_id, status):
            statuses.append(status)

    async def _fake_cancel(timer_id):
        removed.append(timer_id)

    monkeypatch.setattr(cs, "AgentConversationWaitRepository", _Waits)
    monkeypatch.setattr(cs, "cancel_snooze_wake", _fake_cancel)

    service = cs.ConversationService.__new__(cs.ConversationService)
    service.uow = None
    service.conversation_repository = _Conversations()

    async def _append(**kwargs):
        appended.append(kwargs)
        return True

    async def _resume(**kwargs):
        resumed.append("resumed")

    monkeypatch.setattr(service, "append_pause_tool_return", _append)
    monkeypatch.setattr(service, "start_resume_run_if_ready", _resume)

    conversation = SimpleNamespace(id=wait.conversation_id, status=None)
    await service._cancel_active_snooze(conversation=conversation)

    assert wait.status is AgentWaitStatus.CANCELLED
    assert removed == [wait.external_ref]
    assert statuses == [cs.ConversationStatus.STOPPED]
    assert conversation.status is cs.ConversationStatus.STOPPED

    # The paused call still gets a return: a tool call with no return is dropped
    # when history is rebuilt, and the model would see a turn ending mid-thought.
    (call,) = appended
    assert call["tool_call_id"] == wait.tool_call_id
    assert call["tool_result"]["woke_because"] == "CANCELLED"
    assert call["tool_result"]["success"] is False

    # But the agent must not wake. That is the whole point of Stop.
    assert resumed == []
