from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.function.domain.events import (
    FunctionRunCompletedEvent,
    FunctionRunExecutionRequestedEvent,
    FunctionRunFailedEvent,
)
from app.modules.workflow.events import handlers
from app.modules.test_support.fakes import PassthroughEventInbox


class _FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _FakeUoW:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


class _FakeUoWFactory:
    def __call__(self):
        return _FakeUoW()


class _FakeJobQueue:
    def __init__(self):
        self.enqueued: list[tuple[str, dict]] = []

    async def enqueue(self, job_name: str, **kwargs):
        self.enqueued.append((job_name, kwargs))
        return SimpleNamespace(id=kwargs.get("_job_id"))


def _agent_completed_event() -> dict:
    return AgentRunCompletedEvent(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        status=AgentRunStatus.COMPLETED,
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_workflow_ignores_agent_completion_without_waiting_run(monkeypatch):
    class FakeWaitRepository:
        def __init__(self, uow):
            pass

        async def find_active_by_external_ref(self, wait_type, external_ref: str):
            return None

    monkeypatch.setattr(
        handlers,
        "SqlAlchemyWorkflowRunWaitRepository",
        FakeWaitRepository,
    )
    job_queue = _FakeJobQueue()

    await handlers.handle_agent_run_event(
        _agent_completed_event(),
        _FakeLogger(),
        job_queue=job_queue,
        uow_factory=_FakeUoWFactory(),
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.enqueued == []


@pytest.mark.asyncio
async def test_workflow_enqueues_agent_completion_with_waiting_run(monkeypatch):
    class FakeWaitRepository:
        def __init__(self, uow):
            pass

        async def find_active_by_external_ref(self, wait_type, external_ref: str):
            return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(
        handlers,
        "SqlAlchemyWorkflowRunWaitRepository",
        FakeWaitRepository,
    )
    job_queue = _FakeJobQueue()
    event = _agent_completed_event()

    await handlers.handle_agent_run_event(
        event,
        _FakeLogger(),
        job_queue=job_queue,
        uow_factory=_FakeUoWFactory(),
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.enqueued == [
        (
            "resume_workflow_run_for_agent",
            {
                "agent_conversation_id": event["conversation_id"],
                "_job_id": f"workflow-resume-agent:{event['agent_run_id']}",
            },
        )
    ]


@pytest.mark.asyncio
async def test_workflow_enqueues_function_completion_with_output():
    job_queue = _FakeJobQueue()
    event = FunctionRunCompletedEvent(
        run_id=uuid4(),
        function_id=uuid4(),
        output_data={"recorded": True},
        completed_at=datetime.now(),
    ).model_dump(mode="json")

    await handlers.handle_function_run_event(
        event,
        _FakeLogger(),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.enqueued == [
        (
            "resume_workflow_run_for_function",
            {
                "function_run_id": event["run_id"],
                "run_status": "COMPLETED",
                "output": {"recorded": True},
                "_job_id": f"workflow-resume-function:{event['run_id']}:COMPLETED",
            },
        )
    ]


@pytest.mark.asyncio
async def test_workflow_enqueues_function_failure_with_error():
    job_queue = _FakeJobQueue()
    event = FunctionRunFailedEvent(
        run_id=uuid4(),
        function_id=uuid4(),
        error="boom",
        completed_at=datetime.now(),
    ).model_dump(mode="json")

    await handlers.handle_function_run_event(
        event,
        _FakeLogger(),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    assert job_queue.enqueued == [
        (
            "resume_workflow_run_for_function",
            {
                "function_run_id": event["run_id"],
                "run_status": "FAILED",
                "output": {"error": "boom"},
                "_job_id": f"workflow-resume-function:{event['run_id']}:FAILED",
            },
        )
    ]


@pytest.mark.asyncio
async def test_workflow_ignores_non_terminal_function_event():
    job_queue = _FakeJobQueue()
    event = FunctionRunExecutionRequestedEvent(
        run_id=uuid4(), function_id=uuid4()
    ).model_dump(mode="json")

    await handlers.handle_function_run_event(
        event, _FakeLogger(), job_queue=job_queue
    )

    assert job_queue.enqueued == []
