from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from streaq.task import TaskStatus

from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.events.handlers import conversation_title_job_id
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.events import handlers


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, object]] = []

    def info(self, message: str, *args: object) -> None:
        self.messages.append((message, args))


class _JobQueue:
    def __init__(self, status: TaskStatus) -> None:
        self._status = status
        self.abort_called = False
        self.enqueued: list[tuple[str, dict, str | None]] = []

    async def status(self, job_id: str) -> TaskStatus:
        return self._status

    async def abort(self, job_id: str, *, timeout_seconds: float | None = None) -> bool:
        self.abort_called = True
        return True

    async def enqueue(self, task_name: str, *, context: dict, _job_id: str | None = None):
        self.enqueued.append((task_name, context, _job_id))
        return object()


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _ConversationRepository:
    def __init__(self, uow) -> None:
        self.uow = uow

    async def finish_agent_run(
        self,
        *,
        agent_run_id,
        status: AgentRunStatus,
    ):
        return SimpleNamespace(status=status, updated=True)


@pytest.mark.asyncio
async def test_stop_requested_for_queued_run_finishes_without_streaq_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, object]] = []
    realtime: list[tuple[object, dict[str, object]]] = []

    async def publish(stream: str, event: object) -> None:
        published.append((stream, event))

    async def publish_realtime(conversation_id, payload) -> None:
        realtime.append((conversation_id, payload))

    monkeypatch.setattr(handlers, "ConversationRepository", _ConversationRepository)
    monkeypatch.setattr(handlers.EventPublisher, "publish", publish)
    monkeypatch.setattr(handlers, "publish_conversation_event", publish_realtime)

    job_queue = _JobQueue(TaskStatus.SCHEDULED)
    stop_event = AgentRunStopRequestedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        user_id=uuid4(),
    )

    await handlers.handle_agent_control_event(
        stop_event.model_dump(mode="json"),
        fs_logger=_Logger(),
        job_queue=job_queue,
        uow_factory=_UowFactory(),
    )

    assert job_queue.abort_called is False
    assert len(published) == 1
    stream, event = published[0]
    assert stream == AGENT_EVENTS_STREAM
    assert isinstance(event, AgentRunCompletedEvent)
    assert event.status == AgentRunStatus.STOPPED
    assert event.data == {
        "aborted": False,
        "task_status": TaskStatus.SCHEDULED.value,
    }
    assert realtime == [
        (
            stop_event.conversation_id,
            {
                "type": "completed",
                "agent_run_id": str(stop_event.agent_run_id),
                "data": {
                    "conversation_id": str(stop_event.conversation_id),
                    "status": AgentRunStatus.STOPPED.value,
                    "aborted": False,
                    "task_status": TaskStatus.SCHEDULED.value,
                },
            },
        )
    ]


def test_title_task_is_registered_on_worker() -> None:
    # Importing ``handlers`` (top of this module) runs the @streaq_task
    # decorators, so the worker the subprocess runs knows the title task.
    from app.core.infrastructure.jobs.streaq_runtime import streaq_worker

    assert "generate_conversation_title" in streaq_worker.registry
    assert "process_agent_run" in streaq_worker.registry


@pytest.mark.asyncio
async def test_completed_event_enqueues_dedup_title_job() -> None:
    job_queue = _JobQueue(TaskStatus.SCHEDULED)
    completed_event = AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    )

    await handlers.handle_agent_control_event(
        completed_event.model_dump(mode="json"),
        fs_logger=_Logger(),
        job_queue=job_queue,
        uow_factory=_UowFactory(),
    )

    assert job_queue.enqueued == [
        (
            "generate_conversation_title",
            {"conversation_id": str(completed_event.conversation_id)},
            conversation_title_job_id(completed_event.conversation_id),
        )
    ]


@pytest.mark.asyncio
async def test_stop_requested_for_running_run_is_left_for_cooperative_stop() -> None:
    job_queue = _JobQueue(TaskStatus.RUNNING)
    stop_event = AgentRunStopRequestedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        user_id=uuid4(),
    )

    await handlers.handle_agent_control_event(
        stop_event.model_dump(mode="json"),
        fs_logger=_Logger(),
        job_queue=job_queue,
        uow_factory=_UowFactory(),
    )

    assert job_queue.abort_called is False


def test_reconcile_orphaned_agent_runs_cron_registered() -> None:
    from app.core.infrastructure.jobs.streaq_runtime import streaq_worker

    assert "reconcile_orphaned_agent_runs" in streaq_worker.registry


@pytest.mark.asyncio
async def test_reconcile_orphaned_agent_runs_finalizes_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconciler marks stale runs FAILED and publishes lifecycle + SSE
    events only for runs it actually transitioned (idempotent under races)."""
    conv1, run1 = uuid4(), uuid4()
    conv2, run2 = uuid4(), uuid4()
    stale = [
        SimpleNamespace(id=run1, conversation_id=conv1),
        SimpleNamespace(id=run2, conversation_id=conv2),
    ]
    finished: list[object] = []
    published: list[tuple[str, object]] = []
    realtime: list[tuple[object, dict]] = []

    class _Repo:
        def __init__(self, uow) -> None:
            self.uow = uow

        async def list_stale_active_runs(self, *, cutoff_seconds, limit=200):
            return stale

        async def finish_agent_run(self, *, agent_run_id, status, error=None):
            finished.append(agent_run_id)
            # run2 was already terminal (race) -> not updated -> no events.
            return SimpleNamespace(updated=agent_run_id == run1, status=status)

    async def publish(stream: str, event: object) -> None:
        published.append((stream, event))

    async def publish_realtime(conversation_id, payload) -> None:
        realtime.append((conversation_id, payload))

    monkeypatch.setattr(handlers, "ConversationRepository", _Repo)
    monkeypatch.setattr(handlers.EventPublisher, "publish", publish)
    monkeypatch.setattr(handlers, "publish_conversation_event", publish_realtime)
    monkeypatch.setattr(
        handlers,
        "streaq_worker",
        SimpleNamespace(context=SimpleNamespace(uow=lambda: _UowFactory())),
    )

    await handlers.reconcile_orphaned_agent_runs()

    # Both stale runs were attempted...
    assert finished == [run1, run2]
    # ...but only the one that actually transitioned publishes events.
    assert len(published) == 1
    stream, event = published[0]
    assert stream == AGENT_EVENTS_STREAM
    assert isinstance(event, AgentRunCompletedEvent)
    assert event.agent_run_id == run1
    assert event.status == AgentRunStatus.FAILED
    assert [cid for cid, _ in realtime] == [conv1]
