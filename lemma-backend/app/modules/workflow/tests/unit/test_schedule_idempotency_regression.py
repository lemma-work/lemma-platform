"""Regression tests for the workflow+schedule idempotency fixes.

Covers run-before-side-effect persistence, durable fire deduplication, and
single-owner target dispatch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.workflow.domain.events import WorkflowRunTerminalEvent
from app.modules.workflow.domain.run import WorkflowRunEntity
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.services.schedule_start_service import ScheduleStartService


def _schedule(**overrides) -> SimpleNamespace:
    """A stand-in schedule row, with every field the dispatcher reads.

    One factory rather than six inline namespaces: the dispatcher's view of a
    schedule grows, and six copies means six places to miss. ``has_target`` is
    a property on the real entity and a plain attribute here, so it is derived
    from the same three columns the entity derives it from.
    """
    fields = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "workflow_id": None,
        "agent_id": None,
        "targets_pod_default": False,
        "instruction": None,
        "is_active": True,
        "schedule_type": SimpleNamespace(value="TIME"),
        "config": {},
        **overrides,
    }
    fields["has_target"] = (
        fields["agent_id"] is not None
        or fields["workflow_id"] is not None
        or fields["targets_pod_default"]
    )
    return SimpleNamespace(**fields)


def _engine_with_mocks() -> WorkflowEngine:
    uow = Mock()
    uow.commit = AsyncMock()
    uow.session = Mock()
    engine = WorkflowEngine(
        uow,
        agent_adapter=Mock(),
        function_adapter=Mock(),
        schedule_adapter=Mock(),
    )
    return engine


@pytest.mark.anyio
async def test_start_run_persists_row_before_advancing():
    """The run row must be created/flushed (under the unique constraint) before
    stepper.advance runs any node side effects (LP-057)."""
    engine = _engine_with_mocks()

    flow_id, pod_id, user_id = uuid4(), uuid4(), uuid4()
    engine.flow_repo.get = AsyncMock(
        return_value=SimpleNamespace(id=flow_id, pod_id=pod_id)
    )
    engine._require_action = AsyncMock(return_value=None)
    engine._entry_node_id = Mock(return_value="entry")

    engine.run_repo = AsyncMock()
    engine.run_repo.update.side_effect = lambda run: run

    stepper = Mock()
    stepper.advance = AsyncMock(return_value=SimpleNamespace(wait=None))
    engine._stepper = Mock(return_value=stepper)

    # Record the relative order of create vs advance.
    order = Mock()
    order.attach_mock(engine.run_repo.create, "create")
    order.attach_mock(stepper.advance, "advance")

    await engine.start_run(flow_id, user_id)

    engine.run_repo.create.assert_awaited_once()
    stepper.advance.assert_awaited_once()
    call_names = [c[0] for c in order.mock_calls]
    assert call_names.index("create") < call_names.index("advance"), (
        "run row must be persisted before node side effects run"
    )


@pytest.mark.anyio
async def test_start_run_emits_transactional_terminal_event():
    engine = _engine_with_mocks()
    flow_id, pod_id, user_id = uuid4(), uuid4(), uuid4()
    engine.flow_repo.get = AsyncMock(
        return_value=SimpleNamespace(id=flow_id, pod_id=pod_id)
    )
    engine._require_action = AsyncMock(return_value=None)
    engine._entry_node_id = Mock(return_value="entry")
    engine.run_repo = AsyncMock()
    engine.run_repo.update.side_effect = lambda run: run

    async def complete(run, _flow):
        run.complete()
        return SimpleNamespace(wait=None)

    stepper = Mock(advance=AsyncMock(side_effect=complete))
    engine._stepper = Mock(return_value=stepper)

    run = await engine.start_run(flow_id, user_id)

    event = engine.uow.collect_events.call_args.args[0][0]
    assert isinstance(event, WorkflowRunTerminalEvent)
    assert event.run_id == run.id
    assert event.status.value == "COMPLETED"


@pytest.mark.anyio
async def test_reserved_workflow_run_id_is_idempotent():
    engine = _engine_with_mocks()
    run_id, flow_id, pod_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
    existing = WorkflowRunEntity(
        id=run_id,
        flow_id=flow_id,
        pod_id=pod_id,
        user_id=user_id,
        status="RUNNING",
    )
    engine.flow_repo.get = AsyncMock(
        return_value=SimpleNamespace(id=flow_id, pod_id=pod_id)
    )
    engine._require_action = AsyncMock(return_value=None)
    engine._entry_node_id = Mock(return_value="entry")
    engine.run_repo = AsyncMock(get=AsyncMock(return_value=existing))
    engine._stepper = Mock()

    result = await engine.start_run(flow_id, user_id, run_id=run_id)

    assert result is existing
    engine.run_repo.create.assert_not_awaited()
    engine._stepper.assert_not_called()


@pytest.mark.anyio
async def test_duplicate_agent_schedule_fire_is_skipped(monkeypatch):
    """A redelivered agent-target fire whose ledger claim fails must not start a
    second conversation (LP-102)."""
    engine = _engine_with_mocks()
    engine.agent_adapter.run_agent_by_id = AsyncMock(return_value=uuid4())

    schedule = _schedule(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        workflow_id=None,
        agent_id=uuid4(),
        is_active=True,
        schedule_type=SimpleNamespace(value="TIME"),
    )

    svc = ScheduleStartService(engine)

    # Schedule lookup returns our agent-target schedule.
    import app.modules.workflow.services.schedule_start_service as repo_mod

    monkeypatch.setattr(
        repo_mod,
        "ScheduleRepository",
        lambda uow: Mock(get=AsyncMock(return_value=schedule)),
    )

    # The durable dedup claim reports "already delivered".
    import app.modules.workflow.services.schedule_start_service as run_repo_mod

    run_repo = Mock()
    run_repo.claim = AsyncMock(return_value=None)
    monkeypatch.setattr(run_repo_mod, "ScheduleRunRepository", lambda uow: run_repo)

    await svc.handle_schedule_fired(
        schedule_id=str(schedule.id),
        user_id=schedule.user_id,
        payload={},
        schedule_event_id="evt-1",
    )

    run_repo.claim.assert_awaited_once()
    engine.agent_adapter.run_agent_by_id.assert_not_awaited()


@pytest.mark.anyio
async def test_emitted_one_time_fire_runs_after_schedule_is_marked_inactive(
    monkeypatch,
):
    engine = _engine_with_mocks()
    scheduled_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
    schedule = _schedule(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        workflow_id=uuid4(),
        agent_id=None,
        is_active=False,
        schedule_type=ScheduleType.TIME,
        config={"scheduled_at": scheduled_at.isoformat()},
    )
    schedule_repo = Mock(get=AsyncMock(return_value=schedule))
    run_repo = Mock(claim=AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRepository",
        lambda uow: schedule_repo,
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRunRepository",
        lambda uow: run_repo,
    )

    await ScheduleStartService(engine).handle_schedule_fired(
        schedule_id=str(schedule.id),
        user_id=schedule.user_id,
        payload={},
        schedule_event_id=f"cron:{schedule.id}:{scheduled_at.isoformat()}",
        source_occurred_at=scheduled_at,
    )

    run_repo.claim.assert_awaited_once()


@pytest.mark.anyio
async def test_workflow_timer_requires_exact_wait_ref() -> None:
    service = ScheduleStartService(_engine_with_mocks())

    with pytest.raises(ValueError, match="wait_ref is required"):
        await service.handle_schedule_fired(
            schedule_id=str(uuid4()),
            user_id=uuid4(),
            payload={"workflow_run_id": str(uuid4())},
            schedule_event_id="timer:event-1",
        )


@pytest.mark.anyio
async def test_workflow_timer_rejects_non_owner_user() -> None:
    engine = _engine_with_mocks()
    run_id = uuid4()
    engine.run_repo.get = AsyncMock(
        return_value=SimpleNamespace(
            id=run_id,
            user_id=uuid4(),
            pod_id=uuid4(),
        )
    )
    engine.resume_internal = AsyncMock()
    service = ScheduleStartService(engine)

    with pytest.raises(ValueError, match="does not match the run owner"):
        await service.handle_schedule_fired(
            schedule_id=str(uuid4()),
            user_id=uuid4(),
            payload={
                "workflow_run_id": str(run_id),
                "wait_ref": str(uuid4()),
            },
            schedule_event_id="timer:event-2",
        )

    engine.resume_internal.assert_not_awaited()


@pytest.mark.anyio
async def test_agent_schedule_run_and_conversation_use_event_user(monkeypatch):
    engine = _engine_with_mocks()
    conversation_id = uuid4()
    engine.agent_adapter.run_agent_by_id = AsyncMock(return_value=conversation_id)
    schedule = _schedule(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        workflow_id=None,
        agent_id=uuid4(),
        is_active=True,
        schedule_type=SimpleNamespace(value="DATASTORE"),
    )
    row_owner_id = uuid4()
    target_run_id = uuid4()
    schedule_run = SimpleNamespace(
        id=uuid4(),
        user_id=row_owner_id,
        status=ScheduleRunStatus.PROCESSING,
        target_run_id=str(target_run_id),
    )
    schedule_repo = Mock(get=AsyncMock(return_value=schedule))
    run_repo = Mock(
        claim=AsyncMock(return_value=schedule_run),
        mark_dispatched=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRepository",
        lambda uow: schedule_repo,
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRunRepository",
        lambda uow: run_repo,
    )
    context = AsyncMock()
    service = ScheduleStartService(engine)
    service._build_user_context = AsyncMock(return_value=context)
    service._record_fire = AsyncMock()

    await service.handle_schedule_fired(
        schedule_id=str(schedule.id),
        user_id=row_owner_id,
        payload={"id": "row-1"},
        schedule_event_id="datastore:event-1",
    )

    assert run_repo.claim.await_args.kwargs["user_id"] == row_owner_id
    assert (
        engine.agent_adapter.run_agent_by_id.await_args.kwargs["user_id"]
        == row_owner_id
    )
    assert (
        engine.agent_adapter.run_agent_by_id.await_args.kwargs["conversation_id"]
        == target_run_id
    )
    context.require.assert_awaited_once()
    run_repo.mark_dispatched.assert_awaited_once_with(schedule_run.id)


@pytest.mark.anyio
async def test_workflow_schedule_run_uses_event_user(monkeypatch):
    engine = _engine_with_mocks()
    schedule = _schedule(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        workflow_id=uuid4(),
        agent_id=None,
        is_active=True,
        schedule_type=SimpleNamespace(value="DATASTORE"),
    )
    row_owner_id = uuid4()
    target_run_id = uuid4()
    schedule_run = SimpleNamespace(
        id=uuid4(),
        user_id=row_owner_id,
        status=ScheduleRunStatus.PROCESSING,
        target_run_id=str(target_run_id),
    )
    schedule_repo = Mock(get=AsyncMock(return_value=schedule))
    run_repo = Mock(
        claim=AsyncMock(return_value=schedule_run),
        mark_dispatched=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRepository",
        lambda uow: schedule_repo,
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRunRepository",
        lambda uow: run_repo,
    )
    service = ScheduleStartService(engine)
    service._start_workflow_for_schedule = AsyncMock(return_value="workflow-run-1")
    service._record_fire = AsyncMock()

    await service.handle_schedule_fired(
        schedule_id=str(schedule.id),
        user_id=row_owner_id,
        payload={"id": "row-1"},
        schedule_event_id="datastore:event-2",
    )

    assert run_repo.claim.await_args.kwargs["user_id"] == row_owner_id
    assert (
        service._start_workflow_for_schedule.await_args.kwargs["user_id"]
        == row_owner_id
    )
    assert service._start_workflow_for_schedule.await_args.kwargs[
        "target_run_id"
    ] == str(target_run_id)


@pytest.mark.anyio
async def test_unauthorized_event_user_fails_agent_schedule_without_fallback(
    monkeypatch,
):
    engine = _engine_with_mocks()
    engine.agent_adapter.run_agent_by_id = AsyncMock()
    schedule = _schedule(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        workflow_id=None,
        agent_id=uuid4(),
        is_active=True,
        schedule_type=SimpleNamespace(value="DATASTORE"),
    )
    row_owner_id = uuid4()
    schedule_run = SimpleNamespace(
        id=uuid4(),
        user_id=row_owner_id,
        status=ScheduleRunStatus.PROCESSING,
        target_run_id=str(uuid4()),
    )
    schedule_repo = Mock(get=AsyncMock(return_value=schedule))
    run_repo = Mock(
        claim=AsyncMock(return_value=schedule_run),
        mark_failed=AsyncMock(return_value=ScheduleRunStatus.FAILED),
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRepository",
        lambda uow: schedule_repo,
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRunRepository",
        lambda uow: run_repo,
    )
    context = AsyncMock()
    context.require.side_effect = PermissionError("agent.execute denied")
    service = ScheduleStartService(engine)
    service._build_user_context = AsyncMock(return_value=context)
    service._record_fire = AsyncMock()

    with pytest.raises(PermissionError, match="agent.execute denied"):
        await service.handle_schedule_fired(
            schedule_id=str(schedule.id),
            user_id=row_owner_id,
            payload={"id": "row-1"},
            schedule_event_id="datastore:event-3",
        )

    engine.agent_adapter.run_agent_by_id.assert_not_awaited()
    run_repo.mark_failed.assert_awaited_once()
    assert run_repo.claim.await_args.kwargs["user_id"] == row_owner_id
    assert service._record_fire.await_args.kwargs["dispatch_dead_lettered"] is False


@pytest.mark.anyio
async def test_workflow_timer_without_owner_adopts_the_run_owner() -> None:
    """Wait timers persisted before ownership existed must still wake their run.

    ``reconcile_time_schedule_jobs`` rewrites logical schedule jobs with an
    owner at startup but deliberately leaves wait timers alone, so a timer
    scheduled by an older deployment fires with no user_id. The run row is the
    authoritative owner, so nothing needs to be synthesized and the wake
    proceeds instead of hanging forever.
    """
    engine = _engine_with_mocks()
    run_id, owner, pod_id = uuid4(), uuid4(), uuid4()
    engine.run_repo.get = AsyncMock(
        return_value=SimpleNamespace(id=run_id, user_id=owner, pod_id=pod_id)
    )
    engine.resume_internal = AsyncMock()
    service = ScheduleStartService(engine)
    service._build_user_context = AsyncMock(return_value=Mock())
    wait_ref = str(uuid4())

    await service.handle_schedule_fired(
        schedule_id=str(uuid4()),
        user_id=None,
        payload={"workflow_run_id": str(run_id), "wait_ref": wait_ref},
        schedule_event_id="timer:legacy-1",
    )

    engine.resume_internal.assert_awaited_once()
    assert engine.resume_internal.await_args.kwargs["external_ref"] == wait_ref
    # The run owner is what authorizes the resume, not anything from the timer.
    assert service._build_user_context.await_args.kwargs["user_id"] == owner


@pytest.mark.anyio
async def test_legacy_time_schedule_fire_resolves_owner_from_schedule(
    monkeypatch,
) -> None:
    engine = _engine_with_mocks()
    schedule = _schedule(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        workflow_id=uuid4(),
        agent_id=None,
        is_active=True,
        schedule_type=SimpleNamespace(value="TIME"),
    )
    import app.modules.workflow.services.schedule_start_service as svc_mod

    schedule_repo = Mock(
        get=AsyncMock(return_value=schedule),
        record_fire=AsyncMock(),
    )
    monkeypatch.setattr(svc_mod, "ScheduleRepository", lambda uow: schedule_repo)
    schedule_run = SimpleNamespace(
        id=uuid4(),
        user_id=schedule.user_id,
        target_run_id=str(uuid4()),
        status=ScheduleRunStatus.PROCESSING,
    )
    run_repo = Mock(
        claim=AsyncMock(return_value=schedule_run),
        mark_dispatched=AsyncMock(),
    )
    monkeypatch.setattr(svc_mod, "ScheduleRunRepository", lambda uow: run_repo)
    service = ScheduleStartService(engine)
    service._start_workflow_for_schedule = AsyncMock(
        return_value=schedule_run.target_run_id
    )

    await service.handle_schedule_fired(
        schedule_id=str(schedule.id),
        user_id=None,
        payload={},
        schedule_event_id="evt-no-owner",
    )

    assert run_repo.claim.await_args.kwargs["user_id"] == schedule.user_id
    run_repo.mark_dispatched.assert_awaited_once_with(schedule_run.id)


@pytest.mark.anyio
async def test_unparseable_schedule_fire_is_dropped_by_the_inbox() -> None:
    """Parsing must happen inside the inbox so poison events terminate.

    A ``schedule.fired`` staged by an older deployment can lack the now-required
    ``source_event_id``. Validating before ``inbox.process`` would nack it out
    to the subscriber, and the 60s reclaim loop has no attempt cap — the same
    message would redeliver forever. Inside the inbox it is marked TERMINAL and
    acked once.
    """
    from app.modules.test_support.fakes import ValidationTerminalEventInbox
    from app.modules.workflow.events.handlers import handle_schedule_events

    inbox = ValidationTerminalEventInbox()
    job_queue = Mock(enqueue=AsyncMock())

    await handle_schedule_events(
        {
            "event_type": "schedule.fired",
            "schedule_id": str(uuid4()),
            "user_id": str(uuid4()),
            "schedule_type": "TIME",
            "payload": {},
        },
        Mock(),
        job_queue=job_queue,
        inbox=inbox,
    )

    assert inbox.terminal == ["workflow.schedule-start:ValidationError"]
    job_queue.enqueue.assert_not_awaited()


@pytest.mark.anyio
async def test_owner_survives_the_queue_boundary_as_none_not_the_string_none() -> None:
    """An owner-less fire must reach the worker as None, not ``"None"``.

    The enqueue boundary stringifies its kwargs. ``str(None)`` produces the
    literal ``"None"``, which is truthy, so the legacy-timer branch would try
    ``UUID("None")`` and the run would never wake — the exact failure this
    ownership work is meant to remove.
    """
    from app.modules.schedule.domain.events.schedule import ScheduleFired
    from app.modules.schedule.domain.schedule import ScheduleType
    from app.modules.workflow.events.handlers import on_schedule_fired

    job_queue = Mock(enqueue=AsyncMock())
    owner = uuid4()

    for user_id, expected in ((None, None), (owner, str(owner))):
        job_queue.enqueue.reset_mock()
        await on_schedule_fired(
            ScheduleFired(
                schedule_id=uuid4(),
                user_id=user_id,
                schedule_type=ScheduleType.TIME,
                payload={"workflow_run_id": str(uuid4()), "wait_ref": str(uuid4())},
                source_event_id="timer:boundary-1",
            ),
            Mock(),
            job_queue,
        )
        assert job_queue.enqueue.await_args.kwargs["user_id"] == expected


@pytest.mark.anyio
async def test_pod_default_schedule_starts_the_assistant_with_its_instruction(
    monkeypatch,
):
    """A Lem-targeted schedule dispatches without an agent row.

    The default assistant has no ``agents`` row, so ``run_agent_by_id`` -- which
    looks one up -- can only raise for it. The target is a flag on the schedule,
    and the run it starts is a conversation with a null ``agent_id``: exactly
    what ``resolve_agent`` already synthesises Lem from.

    The instruction rides along because nothing else on the run says what it is
    for. A named agent's standing instruction answers that; Lem's is the empty
    string, so without this the assistant wakes to a JSON payload and no job.
    """
    engine = _engine_with_mocks()
    conversation_id = uuid4()
    engine.agent_adapter.run_pod_default_agent = AsyncMock(return_value=conversation_id)
    engine.agent_adapter.run_agent_by_id = AsyncMock()
    schedule = _schedule(
        agent_id=None,
        workflow_id=None,
        targets_pod_default=True,
        instruction="Summarise yesterday's open tickets.",
        schedule_type=SimpleNamespace(value="TIME"),
    )
    target_run_id = uuid4()
    schedule_run = SimpleNamespace(
        id=uuid4(),
        user_id=schedule.user_id,
        status=ScheduleRunStatus.PROCESSING,
        target_run_id=str(target_run_id),
    )
    schedule_repo = Mock(get=AsyncMock(return_value=schedule))
    run_repo = Mock(
        claim=AsyncMock(return_value=schedule_run),
        mark_dispatched=AsyncMock(),
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRepository",
        lambda uow: schedule_repo,
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRunRepository",
        lambda uow: run_repo,
    )
    context = AsyncMock()
    service = ScheduleStartService(engine)
    service._build_user_context = AsyncMock(return_value=context)
    service._record_fire = AsyncMock()

    await service.handle_schedule_fired(
        schedule_id=str(schedule.id),
        user_id=schedule.user_id,
        payload={},
        schedule_event_id="cron:lem-1",
    )

    engine.agent_adapter.run_agent_by_id.assert_not_awaited()
    call = engine.agent_adapter.run_pod_default_agent.await_args.kwargs
    assert call["conversation_id"] == target_run_id
    assert call["instructions"] == "Summarise yesterday's open tickets."
    # The ledger still records it as an agent target: Lem is an agent, it just
    # has no row. A third `target_kind` would make every consumer of the run
    # ledger learn a distinction that does not exist downstream.
    assert run_repo.claim.await_args.kwargs["target_kind"] == "AGENT"
    run_repo.mark_dispatched.assert_awaited_once_with(schedule_run.id)


@pytest.mark.anyio
async def test_pod_default_schedule_authorizes_against_the_pod_not_an_agent(
    monkeypatch,
):
    """There is no agent resource to authorize against, so the pod is the ref.

    Passing a made-up agent id here would ask about a resource that does not
    exist, and a permission check on a non-existent resource is not a check.
    """
    from app.core.authorization.context import ResourceType

    engine = _engine_with_mocks()
    engine.agent_adapter.run_pod_default_agent = AsyncMock(return_value=uuid4())
    schedule = _schedule(
        targets_pod_default=True,
        instruction="Check the overnight queue.",
        schedule_type=SimpleNamespace(value="TIME"),
    )
    schedule_run = SimpleNamespace(
        id=uuid4(),
        user_id=schedule.user_id,
        status=ScheduleRunStatus.PROCESSING,
        target_run_id=str(uuid4()),
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRepository",
        lambda uow: Mock(get=AsyncMock(return_value=schedule)),
    )
    monkeypatch.setattr(
        "app.modules.workflow.services.schedule_start_service.ScheduleRunRepository",
        lambda uow: Mock(
            claim=AsyncMock(return_value=schedule_run),
            mark_dispatched=AsyncMock(),
        ),
    )
    context = AsyncMock()
    service = ScheduleStartService(engine)
    service._build_user_context = AsyncMock(return_value=context)
    service._record_fire = AsyncMock()

    await service.handle_schedule_fired(
        schedule_id=str(schedule.id),
        user_id=schedule.user_id,
        payload={},
        schedule_event_id="cron:lem-2",
    )

    permission, ref = context.require.await_args.args
    assert permission == "agent.execute"
    assert ref.resource_type is ResourceType.POD
    assert ref.resource_id == schedule.pod_id
