"""Unit tests for `wait_for`: the wait lifecycle and the tool's guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent.services.pause_resume import PauseResume

import app.modules.agent.services.conversation_stop as stops
from app.modules.agent.domain.value_objects import ConversationStatus
import app.modules.agent.tools.waiting.pydantic_adapter as adapter
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitStatus,
    AgentWaitType,
    AgentWaitWakeReason,
)
from app.modules.agent.tools.waiting.models import (
    DEFAULT_TARGET_DEADLINE_SECONDS,
    MAX_WAIT_SECONDS,
    MIN_WAIT_SECONDS,
    WaitForRequest,
)
from app.modules.agent.tools.waiting.pydantic_adapter import wait_for
from app.modules.agent.tools.tool_errors import AgentInputRequired


def _wait(**overrides) -> AgentConversationWaitEntity:
    defaults = {
        "conversation_id": uuid4(),
        "agent_run_id": uuid4(),
        "pod_id": uuid4(),
        "tool_call_id": "tc-1",
    }
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


def test_the_wait_types_are_the_three_an_agent_can_name():
    """Record waits stay cut — a row changing is a datastore trigger's job."""
    assert [member.value for member in AgentWaitType] == [
        "TIME",
        "PROCESS",
        "SUBAGENT",
    ]


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


@pytest.mark.asyncio
async def test_naming_no_target_is_refused():
    """The request model cannot enforce this: all three targets are optional."""
    response = await wait_for(_ctx(), WaitForRequest(reason="waiting"))
    assert response.success is False
    assert "exactly one" in (response.error or "")


@pytest.mark.asyncio
async def test_naming_two_targets_is_refused():
    """Waiting on several things at once is a wait that resolves ambiguously.

    The one-active-wait index means there is nowhere to put the second, so this
    has to be refused in words rather than half-honoured.
    """
    response = await wait_for(
        _ctx(),
        WaitForRequest(reason="waiting", seconds=600, process_id="p-1"),
    )
    assert response.success is False
    assert "exactly one" in (response.error or "")


# -- the tool's guards ---------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_refuses_a_pointless_short_sleep():
    """Rejected, not clamped — a 5s ask means the model misread the tool."""
    response = await wait_for(
        _ctx(), WaitForRequest(reason="waiting", seconds=MIN_WAIT_SECONDS - 1)
    )
    assert response.success is False
    assert "Minimum wait" in (response.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("max_seconds", [0, MIN_WAIT_SECONDS - 1])
async def test_a_ceiling_below_the_minimum_is_refused_not_read_as_the_default(
    max_seconds,
):
    """`max_seconds=0` fell through `or` to the default ceiling: the agent asked
    not to wait at all and was left waiting for the longest time on offer."""
    response = await wait_for(
        _ctx(),
        WaitForRequest(reason="build", process_id="proc-1", max_seconds=max_seconds),
    )

    assert response.success is False
    assert "max_seconds" in (response.error or "")


def test_a_target_wait_gives_up_in_minutes_by_default_not_an_hour():
    assert DEFAULT_TARGET_DEADLINE_SECONDS == 15 * 60


@pytest.mark.asyncio
async def test_wait_for_requires_an_active_run():
    ctx = _ctx()
    ctx.deps.agent_run_id = None
    response = await wait_for(ctx, WaitForRequest(reason="waiting", seconds=600))
    assert response.success is False
    assert "active agent run" in (response.error or "")


@pytest.mark.asyncio
async def test_wait_for_requires_a_pod():
    ctx = _ctx()
    ctx.deps.pod_id = None
    response = await wait_for(ctx, WaitForRequest(reason="waiting", seconds=600))
    assert response.success is False
    assert "inside a pod" in (response.error or "")


@pytest.mark.asyncio
async def test_wait_for_requires_a_durable_tool_call_id():
    response = await wait_for(
        _ctx(tool_call_id=""), WaitForRequest(reason="waiting", seconds=600)
    )
    assert response.success is False
    assert "durable tool call id" in (response.error or "")


def test_ceiling_is_a_day():
    assert MAX_WAIT_SECONDS == 24 * 60 * 60


# -- the suspend path ----------------------------------------------------------


@pytest.fixture
def suspend_harness(monkeypatch):
    """Stub the two writes of a successful wait: the row and its work item.

    Both go in one transaction: a wait with nothing to fire it is an agent that
    sleeps forever.
    """
    created: list[AgentConversationWaitEntity] = []
    armed: list[tuple[AgentConversationWaitEntity, datetime | None]] = []

    class _FakeRepo:
        def __init__(self, uow):
            pass

        async def create(self, wait):
            created.append(wait)
            return wait

        async def find_active_for_run(self, agent_run_id):
            # The tool checks for an existing wait before writing one, because
            # the database's one-active-wait index would otherwise surface as an
            # IntegrityError raised from inside a tool body.
            return None

    class _FakeUow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            pass

    suspended: list = []
    poked: list = []

    async def _fake_suspend_remote_run(uow, *, agent_run_id):
        suspended.append(agent_run_id)
        return "host-1"

    async def _fake_poke_host(host_id):
        poked.append(host_id)

    monkeypatch.setattr(adapter, "AgentConversationWaitRepository", _FakeRepo)
    monkeypatch.setattr(
        adapter, "SessionUnitOfWorkFactory", lambda maker: lambda: _FakeUow()
    )

    async def _fake_arm_wait(work, wait, at):
        armed.append((wait, at))

    monkeypatch.setattr(adapter, "suspend_remote_run", _fake_suspend_remote_run)
    monkeypatch.setattr(adapter, "poke_host", _fake_poke_host)
    monkeypatch.setattr(adapter, "arm_wait", _fake_arm_wait)
    monkeypatch.setattr(adapter.WorkWriter, "of", staticmethod(lambda uow: None))
    return SimpleNamespace(
        created=created, suspended=suspended, poked=poked, armed=armed
    )


@pytest.mark.asyncio
async def test_wait_for_arms_one_active_wait_and_pauses_the_run(suspend_harness):
    """The success path: one ACTIVE wait, its timer item, and the pause signal.

    The row and the item that fires it are written together, the item due at
    the requested time.
    """
    ctx = _ctx()
    before = datetime.now(timezone.utc)
    with pytest.raises(AgentInputRequired) as raised:
        await wait_for(ctx, WaitForRequest(reason="waiting for the build", seconds=600))

    assert raised.value.tool_call_id == "tc-1"
    assert raised.value.kind == "wait_for"

    (wait,) = suspend_harness.created
    assert wait.conversation_id == ctx.deps.conversation_id
    assert wait.status is AgentWaitStatus.ACTIVE
    assert wait.tool_call_id == "tc-1"
    assert wait.spec["note_to_self"] is None
    # Due when it was asked to be. A minute of slack, not a fixed clock, so
    # this does not flake.
    assert (
        timedelta(seconds=599) <= wait.scheduled_at - before <= timedelta(seconds=660)
    )
    # And armed: the item is due exactly when the row says it wakes.
    assert suspend_harness.armed == [(wait, wait.scheduled_at)]
    # And resolvable to exactly one wait: `external_ref` is what
    # `find_active_by_external_ref` joins on.
    assert UUID(wait.external_ref)


@pytest.mark.asyncio
async def test_two_waits_in_one_conversation_cannot_resume_each_other(
    suspend_harness,
):
    """`external_ref` is per-wait, not per-conversation.

    A fired timer resolves through it, so two sequential snoozes sharing a token
    would have the first one to fire wake the wrong turn. Nothing else asserts
    this now that the token is minted at the call site rather than handed back
    by a scheduler.
    """
    ctx = _ctx()
    for _ in range(2):
        with pytest.raises(AgentInputRequired):
            await wait_for(ctx, WaitForRequest(reason="waiting", seconds=600))

    first, second = suspend_harness.created
    assert first.external_ref != second.external_ref


@pytest.mark.asyncio
async def test_a_remote_harness_sleeps_too_and_is_told_to_stop(suspend_harness):
    """The same wait, ended a different way.

    A remote harness cannot catch a pause raised inside its own MCP tool call,
    so Lemma asks the host to end the turn. What it must *not* do is what it
    used to: refuse the sleep and tell the model to give up, which left the one
    tool for "check back on this in an hour" unavailable to every agent running
    on somebody's own machine.
    """
    ctx = _ctx(supports_pause_signal=False)
    response = await wait_for(ctx, WaitForRequest(reason="waiting", seconds=600))

    # Armed exactly as it is in-process: same ACTIVE row, same resolvable token.
    (wait,) = suspend_harness.created
    assert UUID(wait.external_ref)
    assert wait.status is AgentWaitStatus.ACTIVE
    assert wait.tool_call_id == "tc-1"

    # The turn is ended by Lemma, not left to the model to end politely.
    assert suspend_harness.suspended == [ctx.deps.agent_run_id]
    assert suspend_harness.poked == ["host-1"]

    assert response.success is True
    assert "Your turn ends here" in (response.message or "")


@pytest.mark.asyncio
async def test_the_in_process_harness_is_never_asked_to_cancel_itself(
    suspend_harness,
):
    """It raises, which the run loop catches; a cancel would race that."""
    with pytest.raises(AgentInputRequired):
        await wait_for(_ctx(), WaitForRequest(reason="waiting", seconds=600))
    assert suspend_harness.suspended == []


@pytest.mark.asyncio
async def test_a_target_that_cannot_be_read_still_waits_and_says_so(
    suspend_harness, monkeypatch, caplog
):
    """An unreadable probe is "ask again", so the wait is armed anyway -- but
    production logs at INFO, and a wait that should have answered at once and
    did not has to leave a trace someone can see."""
    from app.modules.agent.services import wait_targets

    async def _unreadable(**_):
        return wait_targets.TargetOutcome(reason=None, unreadable=True)

    monkeypatch.setattr(wait_targets, "read_target", _unreadable)

    with caplog.at_level("WARNING"), pytest.raises(AgentInputRequired):
        await wait_for(_ctx(), WaitForRequest(reason="build", process_id="proc-1"))

    assert len(suspend_harness.created) == 1
    assert "agent.wait.target_unreadable_at_suspend.degraded" in caplog.text


@pytest.mark.asyncio
async def test_wait_for_clamps_an_over_long_request(suspend_harness):
    """The ceiling is policy, not a misunderstanding, so it clamps rather than errors."""
    ctx = _ctx()
    with pytest.raises(AgentInputRequired):
        await wait_for(
            ctx, WaitForRequest(reason="waiting", seconds=MAX_WAIT_SECONDS * 10)
        )

    (wait,) = suspend_harness.created
    slept = (
        wait.scheduled_at - datetime.fromisoformat(wait.spec["started_at"])
    ).total_seconds()
    assert slept == pytest.approx(MAX_WAIT_SECONDS, abs=1)
    # The unclamped ask is kept so the wake can see what the model actually wanted.
    assert wait.spec["requested_seconds"] == MAX_WAIT_SECONDS * 10


# -- Stop, on a sleeping agent -------------------------------------------------


@pytest.mark.asyncio
async def test_stop_cancels_the_wait_drops_the_timer_and_does_not_resume(monkeypatch):
    """Stop on a snoozed conversation used to do nothing at all.

    ``stop_conversation`` only acted when there was an active run, and a snoozed
    turn has none by construction — the run ended cleanly when the tool paused
    it. So Stop returned success, the conversation stayed WAITING, and the timer
    still fired later and resumed the agent the user had just stopped.
    """

    wait = _wait(external_ref=str(uuid4()), spec={"note_to_self": "post it"})
    appended: list[dict] = []
    resumed: list[str] = []
    statuses: list[object] = []

    class _Waits:
        def __init__(self, uow):
            pass

        async def find_active_for_conversation(self, conversation_id):
            return wait

        async def claim(self, wait_id):
            return wait if wait.status is AgentWaitStatus.ACTIVE else None

        async def update(self, updated):
            return updated

    disarmed: list = []

    async def _disarm(work, ended):
        disarmed.append(ended.id)

    class _Conversations:
        async def lock_conversation(self, conversation_id):
            return None

        async def set_conversation_status(self, *, conversation_id, status):
            statuses.append(status)

    monkeypatch.setattr(stops, "AgentConversationWaitRepository", _Waits)
    monkeypatch.setattr(stops, "disarm_wait", _disarm)
    monkeypatch.setattr(stops.WorkWriter, "of", staticmethod(lambda uow: None))

    conversations = _Conversations()
    service = stops.StopCoordinator(
        None, conversations, None, None, PauseResume(None, conversations, None)
    )

    async def _append(**kwargs):
        appended.append(kwargs)
        return True

    async def _resume(**kwargs):
        resumed.append("resumed")

    monkeypatch.setattr(service.pauses, "append_pause_tool_return", _append)
    monkeypatch.setattr(service.pauses, "start_resume_run_if_ready", _resume)

    conversation = SimpleNamespace(id=wait.conversation_id, status=None)
    await service._cancel_active_wait(conversation=conversation)

    # CANCELLED, and its queued timer dropped in the same transaction; a
    # timer already running finds the wait ended under the row lock.
    assert wait.status is AgentWaitStatus.CANCELLED
    assert disarmed == [wait.id]
    assert statuses == [ConversationStatus.STOPPED]
    assert conversation.status is ConversationStatus.STOPPED

    # The paused call still gets a return: a tool call with no return is dropped
    # when history is rebuilt, and the model would see a turn ending mid-thought.
    (call,) = appended
    assert call["tool_call_id"] == wait.tool_call_id
    assert call["tool_result"]["woke_because"] == "CANCELLED"
    assert call["tool_result"]["success"] is False

    # But the agent must not wake. That is the whole point of Stop.
    assert resumed == []
