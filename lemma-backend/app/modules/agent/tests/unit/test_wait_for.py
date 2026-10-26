"""Unit tests for `wait_for`: the wait lifecycle and the tool's guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.modules.agent.application.pauses.supersede as supersede
import app.modules.agent.infrastructure.tools.waiting.tools as adapter
from app.modules.agent.domain.pauses import Pause
from app.modules.agent.domain.state_kinds import PauseKind, PauseStatus
from app.modules.agent.domain.wait import (
    AgentWaitType,
    WaitSpec,
)
from app.modules.agent.infrastructure.tools.waiting.models import (
    DEFAULT_TARGET_DEADLINE_SECONDS,
    MAX_WAIT_SECONDS,
    MIN_WAIT_SECONDS,
    WaitForRequest,
)
from app.modules.agent.infrastructure.tools.waiting.tools import wait_for
from app.modules.agent.domain.tools import AgentInputRequired


def _wait(**overrides) -> Pause:
    defaults = {
        "conversation_id": uuid4(),
        "run_id": uuid4(),
        "pod_id": uuid4(),
        "tool_call_id": "tc-1",
        "tool_name": "wait_for",
        "kind": PauseKind.WAIT,
        "wait_type": AgentWaitType.TIME,
        "wait_spec": WaitSpec(),
    }
    return Pause(**{**defaults, **overrides})


def _ctx(*, supports_pause_signal: bool = True, tool_call_id: str = "tc-1"):
    return SimpleNamespace(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        supports_pause_signal=supports_pause_signal,
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
    ctx.agent_run_id = None
    response = await wait_for(ctx, WaitForRequest(reason="waiting", seconds=600))
    assert response.success is False
    assert "active agent run" in (response.error or "")


@pytest.mark.asyncio
async def test_wait_for_requires_a_pod():
    ctx = _ctx()
    ctx.pod_id = None
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
    created: list[Pause] = []
    armed: list[tuple[Pause, datetime | None]] = []

    class _FakeRepo:
        def __init__(self, uow):
            pass

        async def open(self, wait):
            created.append(wait)
            return True

        async def pending(self, conversation_id=None, **_):
            # The tool checks for an existing wait before writing one, because
            # the database's one-pending-wait index would otherwise surface as
            # an IntegrityError raised from inside a tool body.
            return []

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

    monkeypatch.setattr(adapter, "Pauses", _FakeRepo)
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
    assert wait.conversation_id == ctx.conversation_id
    assert wait.status is PauseStatus.PENDING
    assert wait.tool_call_id == "tc-1"
    assert wait.wait_spec.note_to_self is None
    # Due when it was asked to be. A minute of slack, not a fixed clock, so
    # this does not flake.
    assert timedelta(seconds=599) <= wait.wake_at - before <= timedelta(seconds=660)
    # And armed: the item is due exactly when the row says it wakes.
    assert suspend_harness.armed == [(wait, wait.wake_at)]
    # And resolvable to exactly one wait: a per-wait token.
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
    assert wait.status is PauseStatus.PENDING
    assert wait.tool_call_id == "tc-1"

    # The turn is ended by Lemma, not left to the model to end politely.
    assert suspend_harness.suspended == [ctx.agent_run_id]
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
    slept = (wait.wake_at - wait.wait_spec.started_at).total_seconds()
    assert slept == pytest.approx(MAX_WAIT_SECONDS, abs=1)
    # The unclamped ask is kept so the wake can see what the model actually wanted.
    assert wait.wait_spec.requested_seconds == MAX_WAIT_SECONDS * 10


# -- Stop or a new message, on a sleeping agent ---------------------------------


@pytest.mark.asyncio
async def test_moving_past_a_wait_cancels_it_and_drops_its_timer(monkeypatch):
    """Stop on a snoozed conversation used to do nothing at all.

    A snoozed turn has no active run by construction -- the run ended cleanly
    when the tool paused it -- so Stop returned success and the timer still
    fired later, resuming the agent the user had just stopped. Stop and a new
    message both close what the conversation sits on (``SupersedeStalePauses``).
    """
    wait = _wait(external_ref=str(uuid4()), wait_spec=WaitSpec(note_to_self="post it"))
    appended: list = []
    disarmed: list = []

    class _Pauses:
        async def pending(self, conversation_id=None, *, kinds=(), **_):
            return [wait] if PauseKind.WAIT in kinds and wait.is_open else []

        async def by_id(self, pause_id, *, lock=False):
            return wait

        async def save(self, pause):
            return True

    class _Conversations:
        async def lock_conversation(self, conversation_id):
            return None

    class _Messages:
        async def append_message(self, **kwargs):
            appended.append(kwargs)
            return kwargs

    async def _disarm(work, ended):
        disarmed.append(ended.id)

    monkeypatch.setattr(supersede, "disarm_wait", _disarm)
    monkeypatch.setattr(supersede.WorkWriter, "of", staticmethod(lambda uow: None))
    use_case = supersede.SupersedeStalePauses(None, resume_returns=None)
    use_case.pauses, use_case.conversations = _Pauses(), _Conversations()
    use_case.messages = _Messages()

    conversation = SimpleNamespace(id=wait.conversation_id)
    written = await use_case.supersede(conversation=conversation, user_id=uuid4())

    # CANCELLED, and its queued timer dropped in the same transaction; a
    # timer already running finds the wait ended under the row lock.
    assert wait.status is PauseStatus.CANCELLED
    assert disarmed == [wait.id]
    # The paused call still gets a return: a tool call with no return is dropped
    # when history is rebuilt, and the model would see a turn ending mid-thought.
    (call,) = appended
    assert written == [call]
    assert call["draft"].tool_call_id == wait.tool_call_id
