from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRunUsage,
    ConversationStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.services import run_finalizer as finalizer_module
from app.modules.agent.services.run_finalizer import (
    finalize_safely,
    rejected_run_error_message,
)
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_usage_recorder import RunUsageRecorder
from app.modules.agent.services.agent_runner_service import (
    AgentRunnerService,
    _run_input_text,
)
from app.modules.test_support.fakes import FakeUnitOfWork
from app.modules.usage.contracts import UsageReservation


_GENERIC_REJECTION = "The Agent Host rejected this run before dispatch. Try again."


def test_rejected_run_error_message_uses_the_harness_supplied_detail():
    assert (
        rejected_run_error_message({"detail": "Harness snapshot is stale; refresh it"})
        == "Harness snapshot is stale; refresh it"
    )


def test_rejected_run_error_message_falls_back_for_malformed_data():
    assert rejected_run_error_message("not-a-dict") == _GENERIC_REJECTION
    assert (
        rejected_run_error_message({"reason": "something_else"}) == _GENERIC_REJECTION
    )
    assert rejected_run_error_message({"detail": "   "}) == _GENERIC_REJECTION


class _FailingContextManager:
    """Async context manager that raises on enter, simulating a dead DB session."""

    async def __aenter__(self) -> None:
        raise RuntimeError("db connection lost during shutdown")

    async def __aexit__(self, *args: object) -> None:
        pass


class _FailingUowFactory:
    """Simulates a DB connection that is already closing during worker shutdown."""

    def __call__(self) -> _FailingContextManager:
        return _FailingContextManager()


@pytest.mark.asyncio
async def test_finish_agent_run_rethrows_db_errors_for_boundary_retry() -> None:
    """Infrastructure failure must reach the finalization process boundary."""
    service = AgentRunnerService(
        uow_factory=_FailingUowFactory(),
        harness_registry=HarnessRegistry({}),
    )

    with pytest.raises(RuntimeError, match="db connection lost"):
        await service.finalizer.finish(
            run=RunIdentity(
                conversation_id=UUID("00000000-0000-0000-0000-000000000001"),
                agent_run_id=UUID("00000000-0000-0000-0000-000000000002"),
            ),
            status=AgentRunStatus.FAILED,
            error="Something went wrong",
        )


@pytest.mark.asyncio
async def test_finish_agent_run_uses_committed_terminal_state_and_collects_event(
    monkeypatch,
) -> None:
    """Completion publication must reflect the state won by the DB transition."""
    uow = FakeUnitOfWork()

    class _Factory:
        def __call__(self):
            return uow

    finish_result = SimpleNamespace(
        updated=True,
        status=AgentRunStatus.STOPPED,
        conversation_status=ConversationStatus.STOPPED,
    )
    repository = SimpleNamespace(finish_agent_run=AsyncMock(return_value=finish_result))
    publish = AsyncMock()
    monkeypatch.setattr(finalizer_module, "publish_conversation_event", publish)

    service = AgentRunnerService(
        uow_factory=_Factory(),
        harness_registry=HarnessRegistry({}),
        finalizer=finalizer_module.RunFinalizer(
            _Factory(),
            RunUsageRecorder(_Factory()),
            repository_factory=lambda _uow: repository,
        ),
    )
    publish_usage = AsyncMock()
    monkeypatch.setattr(service.finalizer, "publish_usage", publish_usage)
    identity = RunIdentity(
        conversation_id=UUID("00000000-0000-0000-0000-000000000101"),
        agent_run_id=UUID("00000000-0000-0000-0000-000000000102"),
        organization_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    await service.finalizer.finish(
        run=identity,
        status=AgentRunStatus.FAILED,
        conversation_status=ConversationStatus.FAILED,
        error="stale pre-transition error",
        output_data={"partial": True},
    )

    assert uow.committed is True
    assert len(uow.collected_events) == 1
    event = uow.collected_events[0]
    assert event.status is AgentRunStatus.STOPPED
    assert event.data == {
        "output_data": {"partial": True},
        "conversation_status": "STOPPED",
    }
    # A run is scoped to a conversation, so nothing downstream can say which pod
    # it belonged to from the event alone unless the finalizer puts it there --
    # and it already holds all of it, on the identity it is finishing. Leaving it
    # off meant every consumer that wanted a pod loaded the conversation back.
    assert event.pod_id == identity.pod_id
    assert event.organization_id == identity.organization_id
    assert event.agent_id == identity.agent_id
    assert event.user_id == identity.user_id
    assert event.started_at == identity.started_at
    assert publish.await_count == 1
    assert publish.await_args.args[1]["type"] == "completed"
    publish_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_safely_swallows_exceptions() -> None:
    """finalize_safely must swallow all errors (DB, cancellation, etc)."""

    async def boom() -> None:
        raise RuntimeError("DB gone away")

    # Should not raise.
    await finalize_safely(
        boom(), agent_run_id=UUID("00000000-0000-0000-0000-000000000003")
    )


@pytest.mark.asyncio
async def test_finalize_safely_swallows_cancelled_error() -> None:
    """finalize_safely must swallow asyncio.CancelledError without propagating."""

    async def get_cancelled() -> None:
        raise asyncio.CancelledError()

    # Should not raise — this is the whole point: cancellation during
    # finalization must not crash the worker.
    await finalize_safely(
        get_cancelled(), agent_run_id=UUID("00000000-0000-0000-0000-000000000004")
    )


def _stub_run_entry(
    monkeypatch,
    service: AgentRunnerService,
    *,
    conversation,
    agent,
    agent_run,
    resolve_runtime,
) -> None:
    """Stand in for the two loads `execute` performs before it does any work.

    Both are private methods rather than injected collaborators, so a test that
    wants to drive `execute` has no seam but this one. It is written once so the
    file installs the pair at a single site instead of once per test.
    """

    async def _load(*args, **kwargs):
        return conversation, agent, agent_run, []

    monkeypatch.setattr(service, "_load_run_context", _load)
    monkeypatch.setattr(service, "_resolve_agent_runtime", resolve_runtime)


@pytest.mark.asyncio
async def test_execute_re_raises_cancelled_error(monkeypatch) -> None:
    """execute() must let CancelledError out, not swallow it.

    This is the whole resume mechanism. streaq XACKs a task that returned and
    "relinquishes" a cancelled one -- leaving it in the pending list for the
    next worker to reclaim. Swallowing it made every run interrupted by a
    deploy look like a success, so nothing redelivered it and each person had
    to ask again.

    This test previously asserted the opposite, on the grounds that re-raising
    crashes the worker with "Attempted to exit a cancel scope that isn't the
    current task's current cancel scope". Reproduced against a real worker
    under SIGTERM, with and without a shielded cleanup, that did not happen:
    the task was relinquished and a fresh worker reclaimed it.
    """
    service = AgentRunnerService(
        uow_factory=_FailingUowFactory(),
        harness_registry=HarnessRegistry({}),
    )

    # Build minimal domain objects so _load_run_context returns valid values.
    conversation = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        organization_id=UUID("00000000-0000-0000-0000-000000000011"),
        pod_id=UUID("00000000-0000-0000-0000-000000000012"),
        agent_id=UUID("00000000-0000-0000-0000-000000000013"),
    )
    agent = SimpleNamespace(name="test-agent")
    agent_run = SimpleNamespace(
        started_at=None,
        status=AgentRunStatus.RUNNING,
        agent_runtime=None,
        error=None,
    )

    # Make the harness.run / inner try block raise CancelledError by having
    # _resolve_agent_runtime cancel the current task mid-flight.
    async def fake_resolve(*args, **kwargs):
        raise asyncio.CancelledError()

    _stub_run_entry(
        monkeypatch,
        service,
        conversation=conversation,
        agent=agent,
        agent_run=agent_run,
        resolve_runtime=fake_resolve,
    )

    # The critical assertion: it must reach streaq, or the job is acked and
    # the run is lost.
    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            agent_run_id=UUID("00000000-0000-0000-0000-000000000020"),
            user_id=UUID("00000000-0000-0000-0000-000000000021"),
            pod_id=UUID("00000000-0000-0000-0000-000000000022"),
            agent_name="test-agent",
        )


def _message(role: str, kind: MessageKind, text: str | None) -> Message:
    return Message(
        id=uuid4(),
        created_at=datetime.now(UTC),
        conversation_id=uuid4(),
        sequence=0,
        agent_run_id=None,
        role=role,
        kind=kind,
        text=text,
    )


def test_run_input_text_is_the_turn_that_started_the_run():
    """The span's input is this turn, not the transcript it was handed."""
    messages = [
        _message(MessageRole.USER.value, MessageKind.TEXT, "the previous turn"),
        _message(MessageRole.ASSISTANT.value, MessageKind.TEXT, "an earlier answer"),
        _message(MessageRole.USER.value, MessageKind.TEXT, "what changed?"),
        _message(MessageRole.TOOL.value, MessageKind.TOOL_RETURN, "tool output"),
    ]
    assert _run_input_text(messages) == "what changed?"


def test_run_input_text_skips_non_textual_and_blank_user_messages():
    assert _run_input_text([]) is None
    assert (
        _run_input_text(
            [_message(MessageRole.ASSISTANT.value, MessageKind.TEXT, "only the agent")]
        )
        is None
    )
    assert (
        _run_input_text(
            [
                _message(MessageRole.USER.value, MessageKind.TEXT, "the real prompt"),
                _message(MessageRole.USER.value, MessageKind.TEXT, "   "),
                _message(MessageRole.USER.value, MessageKind.THINKING, "not a prompt"),
            ]
        )
        == "the real prompt"
    )


class _CapturingFinalizer:
    """Records what `finish` was told, so the failure path can be inspected."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def finish(self, **kwargs) -> None:
        self.calls.append(kwargs)


async def test_a_cancelled_run_hands_its_reservation_back_without_finalizing(
    monkeypatch,
) -> None:
    """A cancelled worker parks the run; it does not end it.

    A `CancelledError` here is the worker going away -- a SIGTERM, a streaq
    timeout -- not the run being wrong, so the row stays RUNNING for the worker
    that reclaims the job and nothing is announced. What must not survive is the
    spend reservation: the resuming run takes its own, and holding both charges
    one conversation twice for a restart. `agent_run_id` goes with the release so
    the durable copy on the row is claimed in the same transaction, leaving
    nothing for the orphan reconciler to release a second time.

    Cancellation specifically, because that is the only way to reach this branch:
    the harness converts every ordinary `Exception` into an ERROR event, so an
    ordinary failure finishes through the normal terminal path instead. That is
    also why the run has to get all the way to the event pump here -- the usage
    only exists once something has streamed.

    NOTE: the tokens `outcome` is holding at this point are not recorded
    anywhere. That is a known gap against `PS-OPS-003` and is called out in the
    pull request; closing it is a separate decision from parking the run.
    """
    from app.modules.agent.domain.value_objects import HarnessKind
    from app.modules.agent.services import agent_runner_service as runner_module

    spent = AgentRunUsage(model_name="test-model", input_tokens=4000, output_tokens=120)
    conversation = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        type=None,
    )
    agent = SimpleNamespace(name="test-agent", output_schema=None)
    agent_run = SimpleNamespace(
        started_at=None,
        status=AgentRunStatus.RUNNING,
        agent_runtime=None,
        error=None,
    )
    resolved = SimpleNamespace(
        harness_kind=HarnessKind.HARNESS,
        credentials={},
        model=None,
        model_name_for_harness="test-model",
        public_snapshot=lambda: {"profile_id": "system:lemma", "scope": "SYSTEM"},
    )

    class _Harness:
        kind = HarnessKind.HARNESS

        def run(self, **kwargs):
            # Never consumed -- the patched pump raises instead of iterating --
            # but it is built eagerly as an argument to `drive`, so it has to be
            # a real async iterator.
            async def _events():
                return
                yield

            return _events()

    async def spend_then_cancel(*args, **kwargs):
        # Everything between admission and the cancellation: the run reached the
        # model, spent tokens, and then the worker went away.
        kwargs["outcome"].usage_data = spent
        raise asyncio.CancelledError()

    reservation = UsageReservation(
        organization_id=conversation.organization_id,
        user_id=uuid4(),
        amount_usd=0.01,
        counter_ids=[],
    )
    finalizer = _CapturingFinalizer()
    recorder = SimpleNamespace(
        reserve=AsyncMock(return_value=reservation), release=AsyncMock()
    )
    pump = SimpleNamespace(drive=spend_then_cancel)
    service = AgentRunnerService(
        uow_factory=_FailingUowFactory(),
        harness_registry=HarnessRegistry([_Harness()]),
        tool_assembler=SimpleNamespace(assemble=AsyncMock(return_value=[])),
        usage_recorder=recorder,
        finalizer=finalizer,
        event_pump=pump,
    )

    _stub_run_entry(
        monkeypatch,
        service,
        conversation=conversation,
        agent=agent,
        agent_run=agent_run,
        resolve_runtime=AsyncMock(return_value=resolved),
    )
    run_id = uuid4()
    monkeypatch.setattr(
        runner_module,
        "build_run_context",
        AsyncMock(
            return_value=SimpleNamespace(
                vision_mode=None,
                grant_summary=None,
                user_id=uuid4(),
                org_id=conversation.organization_id,
                pod_id=conversation.pod_id,
                conversation_id=conversation.id,
                agent_run_id=run_id,
                workload_type="agent",
                workload_id=conversation.agent_id,
            )
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await service.execute(
            agent_run_id=run_id,
            user_id=uuid4(),
            pod_id=uuid4(),
            agent_name="test-agent",
        )

    assert not finalizer.calls, "a parked run must not be written terminal"
    recorder.release.assert_awaited_once_with(reservation, agent_run_id=run_id)
    # `spent` is what the run had bought by the time it was cancelled; see the
    # note in the docstring about where it does not go.
    assert spent.input_tokens > 0


@pytest.mark.asyncio
async def test_a_run_that_was_already_terminal_is_still_billed(monkeypatch) -> None:
    """The status write is correctly a no-op; the spend is not.

    `finish_agent_run` reports `updated=False` when the row is already terminal --
    most often because the orphan reconciler reaped a long run that was in fact
    still alive, and then the worker finished it anyway. That branch released the
    reservation and returned, so the tokens the run had genuinely bought were
    billed to nobody. See `PS-OPS-003`.

    Driven through a real `RunFinalizer` with the usage recorder injected, so
    what is asserted is the recording the finalizer actually performs rather than
    a stub standing in for its own method.
    """
    uow = FakeUnitOfWork()

    class _Factory:
        def __call__(self):
            return uow

    repository = SimpleNamespace(
        finish_agent_run=AsyncMock(
            return_value=SimpleNamespace(
                updated=False,
                status=AgentRunStatus.FAILED,
                conversation_status=ConversationStatus.FAILED,
            )
        )
    )

    recorder = SimpleNamespace(record=AsyncMock(), release=AsyncMock())
    finalizer = finalizer_module.RunFinalizer(
        _Factory(), recorder, repository_factory=lambda _uow: repository
    )
    spent = AgentRunUsage(model_name="test-model", input_tokens=2500, output_tokens=90)

    await finalizer.finish(
        run=RunIdentity(
            conversation_id=uuid4(),
            agent_run_id=uuid4(),
            pod_id=uuid4(),
            user_id=uuid4(),
        ),
        status=AgentRunStatus.FAILED,
        usage_data=spent,
    )

    recorder.record.assert_awaited_once()
    assert recorder.record.await_args.kwargs["usage_data"] is spent
    recorder.release.assert_not_awaited()
